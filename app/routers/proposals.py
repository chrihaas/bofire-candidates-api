import datetime
from typing import Annotated, List

from bofire.data_models.candidates_api.api import Proposal, ProposalRequest, StateEnum
from bofire.data_models.dataframes.api import Candidates
from fastapi import APIRouter, Depends, HTTPException
from tinydb import Query, TinyDB


router = APIRouter(prefix="/proposals", tags=["proposals"])


DBPATH = "db.json"

db = None


async def get_db():
    """Get the database connection.

    Yields:
        TinyDB: The database connection.
    """
    # todo: handle caching
    db = TinyDB(DBPATH, default=str)
    try:
        yield db
    finally:
        db.close()


def get_proposal_from_db(
    proposal_id: int, db: Annotated[TinyDB, Depends(get_db)]
) -> Proposal:  # type: ignore
    """Get a proposal from the database by its ID.

    Args:
        proposal_id (int): The ID of the proposal to get.
        db (Annotated[TinyDB, Depends]): The database with the stored proposals.

    Raises:
        HTTPException: Status code 404 if the proposal is not found.

    Returns:
        Proposal: The requested proposal.
    """
    dict_proposal = db.get(doc_id=proposal_id)
    if dict_proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return Proposal(**dict_proposal)


@router.post("", response_model=Proposal)
def create_proposal(
    proposal_request: ProposalRequest,
    db: Annotated[TinyDB, Depends(get_db)],  # type: ignore
) -> Proposal:
    """Creates a proposal for candidates.

    Args:
        proposal_request (ProposalRequest): The original request for the proposal.
        db (Annotated[TinyDB, Depends]): The database to store the proposal.

    Returns:
        Proposal: The created proposal.
    """
    proposal = Proposal(**proposal_request.model_dump())
    id = db.insert(proposal.model_dump())

    # Update the db entry and proposal with the new ID
    db.update({"id": id}, doc_ids=[id])
    updated_proposal = Proposal(**db.get(doc_id=id))
    return updated_proposal


@router.get("", response_model=List[Proposal])
def get_proposals(db: Annotated[TinyDB, Depends(get_db)]) -> List[Proposal]:  # type: ignore
    """Get all proposals from the database.

    Args:
        db (Annotated[TinyDB, Depends]): The database with the stored proposals.

    Returns:
        List[Proposal]: A list of all proposals.
    """
    return [Proposal(**d) for d in db.all()]


@router.get("/claim", response_model=Proposal)
def claim_proposal(db: Annotated[TinyDB, Depends(get_db)]) -> Proposal:  # type: ignore
    """Claims the first proposal in the database which is in the state CREATED.

    Args:
        db (Annotated[TinyDB, Depends]): The database to store the proposal.

    Raises:
        HTTPException: Status code 404 if no proposals are available to claim.

    Returns:
        Proposal: The claimed proposal.
    """
    query_list = db.search(Query().state == StateEnum.CREATED)
    if len(query_list) == 0:
        raise HTTPException(status_code=404, detail="No proposals to claim")
    proposal = Proposal(**query_list[0])
    db.update(
        {"state": StateEnum.CLAIMED, "last_updated_at": datetime.datetime.now()},
        doc_ids=[proposal.id],
    )
    updated_proposal = Proposal(**db.get(doc_id=proposal.id))
    return updated_proposal


@router.get("/{proposal_id}", response_model=Proposal)
def get_proposal(proposal_id: int, db: Annotated[TinyDB, Depends(get_db)]) -> Proposal:  # type: ignore
    """Get a proposal by its ID.

    Args:
        proposal_id (int): The ID of the proposal to get.
        db (Annotated[TinyDB, Depends]): The database with the stored proposals.

    Returns:
        Proposal: The requested proposal.
    """
    print("HELLO WORLD")
    proposal = get_proposal_from_db(proposal_id, db)
    return proposal


@router.get("/{proposal_id}/candidates", response_model=Candidates)
def get_candidates(
    proposal_id: int, db: Annotated[TinyDB, Depends(get_db)]
) -> Candidates:  # type: ignore
    """Get the candidates generated by a proposal.

    Args:
        proposal_id (int): The ID of the proposal to get the candidates from.
        db (Annotated[TinyDB, Depends]): The database with the stored proposals.

    Raises:
        HTTPException: Status code 404 if the proposal does not contain candidates.

    Returns:
        Candidates: The candidates generated by the proposal.
    """
    proposal = get_proposal_from_db(proposal_id, db)
    if proposal.candidates is None:
        raise HTTPException(status_code=404, detail="Candidates not found")

    return proposal.candidates


@router.get("/{proposal_id}/state", response_model=StateEnum)
def get_state(proposal_id: int, db: Annotated[TinyDB, Depends(get_db)]) -> StateEnum:  # type: ignore
    """Get the state of a proposal by its ID.

    Args:
        proposal_id (int): The ID of the proposal to get the state from.
        db (Annotated[TinyDB, Depends]): The database with the stored proposals.

    Returns:
        StateEnum: The state of the proposal.
    """
    proposal = get_proposal_from_db(proposal_id, db)
    return proposal.state


@router.post("/{proposal_id}/mark_processed", response_model=StateEnum)
def mark_processed(
    proposal_id: int,
    candidates: Candidates,
    db: Annotated[TinyDB, Depends(get_db)],  # type: ignore
) -> StateEnum:
    """Marks a proposal as processed and stores the candidates.

    Args:
        proposal_id (int): The ID of the proposal to mark as processed.
        candidates (Candidates): The candidates generated by the proposal.
        db (Annotated[TinyDB, Depends]): The database with the stored proposals.

    Raises:
        HTTPException: Status code 400 if the number of candidates does not match the expected number.

    Returns:
        StateEnum: The state of the proposal after marking it as processed.
    """
    proposal = get_proposal_from_db(proposal_id, db)

    if len(candidates.rows) != proposal.n_candidates:
        raise HTTPException(
            status_code=400,
            detail=f"Expected {proposal.n_candidates} candidates, got {len(candidates.rows)}",
        )

    proposal.candidates = candidates
    proposal.last_updated_at = datetime.datetime.now()
    proposal.state = StateEnum.FINISHED
    db.update(proposal.model_dump(), doc_ids=[proposal_id])
    return proposal.state


@router.post("/{proposal_id}/mark_failed", response_model=StateEnum)
def mark_failed(
    proposal_id: int,
    error_message: dict[str, str],
    db: Annotated[TinyDB, Depends(get_db)],  # type: ignore
) -> StateEnum:
    """Marks a proposal as failed and stores the error message.

    Args:
        proposal_id (int): The ID of the proposal to mark as failed.
        error_message (dict[str, str]): The error message for the failed proposal.
        db (Annotated[TinyDB, Depends]): The database with the stored proposals.

    Returns:
        StateEnum: The state of the proposal after marking it as failed.
    """
    proposal = get_proposal_from_db(proposal_id, db)

    proposal.last_updated_at = datetime.datetime.now()
    proposal.state = StateEnum.FAILED
    proposal.error_message = error_message["msg"]
    db.update(proposal.model_dump(), doc_ids=[proposal_id])
    return proposal.state
