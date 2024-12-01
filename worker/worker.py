import json
import logging
import multiprocessing as mp
import time
from typing import Dict, Optional

import bofire.strategies.api as strategies
import requests
from bofire.data_models.dataframes.api import Candidates
from pydantic import BaseModel, model_validator

from bofire_candidates_api.models.proposals import Proposal, StateEnum


class Client(BaseModel):
    """This class is used to interact with the BoFire candidates API."""

    url: str = "http://localhost:8000"

    @model_validator(mode="after")
    def validate_url(self):
        """Validate the URL by checking if the API is reachable.

        Raises:
            ValueError: If the API is not reachable.
        """
        try:
            self.get_version()
        except Exception:
            raise ValueError(f"Could not connect to {self.url}.")
        return self

    @property
    def headers(self) -> Dict[str, str]:
        """Get the headers for the API requests.

        Returns:
            dict: The headers for the API requests.
        """
        return {"accept": "application/json", "Content-Type": "application/json"}

    def get(self, path: str) -> requests.Response:
        """Send a GET request to the API.

        Args:
            path (str): The enpoint to send the request to.

        Returns:
            requests.Response: The response from the API.
        """
        return requests.get(f"{self.url}{path}", headers=self.headers)

    def post(self, path: str, request_body: Dict) -> requests.Response:
        """Send a POST request to the API.

        Args:
            path (str): The endpoint to send the request to.
            request_body (Dict): The body of the request.

        Returns:
            requests.Response: The response from the API.
        """
        return requests.post(
            f"{self.url}{path}", json=request_body, headers=self.headers
        )

    def get_version(self) -> str:
        """Get the version of the API.

        Returns:
            str: The version of the API.
        """
        response = self.get("/versions")
        return response.json()

    def claim_proposal(self) -> Optional[Proposal]:
        """Claim a proposal from the API.

        Returns:
            Optional[Proposal]: The claimed proposal.
        """
        response = self.get("/proposals/claim")
        if response.status_code == 404:
            return None
        loaded_response = json.loads(response.content)
        return Proposal(**loaded_response)

    def mark_processed(self, proposal_id: int, candidates: Candidates) -> StateEnum:
        """Mark a proposal as processed in the API.

        Args:
            proposal_id (int): The ID of the proposal to mark as processed.
            candidates (Candidates): The candidates generated by the proposal.

        Returns:
            StateEnum: The state of the proposal after marking it as processed.
        """
        response = self.post(
            f"/proposals/{proposal_id}/mark_processed",
            request_body=candidates.model_dump(),
        )
        return StateEnum(response.json())

    def mark_failed(self, proposal_id: int, error_message: str) -> StateEnum:
        """Mark a proposal as failed in the API.

        Args:
            proposal_id (int): The ID of the proposal to mark as failed.
            error_message (str): The error message to store.

        Returns:
            StateEnum: The state of the proposal after marking it as failed.
        """
        response = self.post(
            f"/proposals/{proposal_id}/mark_failed", request_body={"msg": error_message}
        )
        return StateEnum(response.json())


class Worker(BaseModel):
    """This class is used to process proposals from the BoFire candidates API."""

    client: Client
    job_check_interval: float
    round: int = 0

    def sleep(self, sleep_time_sec: float, msg: str = ""):
        """Sleep for a given amount of time.

        Args:
            sleep_time_sec (float): The amount of time to sleep in seconds.
            msg (str, optional): A message to log. Defaults to "".
        """
        logging.debug(f"Sleeping for {sleep_time_sec} second(s) ({msg})")
        time.sleep(sleep_time_sec)

    def work(self):
        """Start processing proposals from the API."""
        while True:
            self.work_round()

    @staticmethod
    def process_proposal(
        proposal: Proposal,
        conn_obj: "mp.connection.Connection",
    ):
        """Process a proposal.

        Args:
            proposal (Proposal): The proposal to process.
            conn_obj (mp.connection.Connection): The connection object to send the results to.
        """
        try:
            strategy = strategies.map(proposal.strategy_data)
            if proposal.experiments is not None:
                strategy.tell(proposal.experiments.to_pandas())
            df_candidates = strategy.ask(proposal.n_candidates)
            msg = Candidates.from_pandas(df_candidates, proposal.strategy_data.domain)
        except Exception as e:
            msg = Exception(str(e))
        finally:
            conn_obj.send(msg)

    def work_round(self):
        """Worker round of processing proposals.

        Raises:
            Exception: If an error occurs while processing the proposal.
        """
        logging.debug(f"Starting round {self.round}")
        self.round += 1
        proposal = self.client.claim_proposal()
        if proposal is None:
            logging.debug("No proposal to work on")
            self.sleep(self.job_check_interval, msg="No proposal to work on.")
            return

        logging.info(f"Claimed proposal {proposal.id}")

        try:
            receiver, sender = mp.Pipe(False)
            proc = mp.Process(
                target=self.process_proposal,
                args=(
                    proposal,
                    sender,
                ),
            )
            proc.start()

            while True:
                if receiver.poll(timeout=self.job_check_interval):
                    candidates = receiver.recv()
                    if isinstance(candidates, Exception):
                        raise candidates
                    else:
                        self.client.mark_processed(proposal.id, candidates=candidates)
                        logging.info(f"Proposal {proposal.id} processed successfully")
                        break
        except Exception as e:
            logging.error(f"Error processing proposal {proposal.id}: {e}")
            self.client.mark_failed(proposal.id, error_message=str(e))
