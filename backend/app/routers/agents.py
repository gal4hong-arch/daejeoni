from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth_deps import get_current_user_id
from app.db.session import get_db
from app.schemas.api import DocumentChainIn, SimulationChainIn
from app.security_stream import get_owned_topic
from app.services.agent_chains import run_document_agent_chain, run_simulation_agent_chain
from app.services.audit_log import audit
from app.services.model_resolver import resolve_model

router = APIRouter(prefix="/agents", tags=["agents"])

_KINDS_DOC = ("report", "memo", "simulation", "explanation", "council")


@router.post("/auth/document-chain")
def document_chain_auth(
    body: DocumentChainIn,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    if body.kind not in _KINDS_DOC:
        raise HTTPException(400, f"kind in {_KINDS_DOC}")
    t = get_owned_topic(db, body.topic_id, user_id)
    model = resolve_model(db, user_id=user_id, topic_session_id=body.topic_id, task="report")
    audit(db, user_id=user_id, action="agent.document_chain.start", detail={"topic_id": body.topic_id})
    db.commit()
    try:
        out = run_document_agent_chain(
            db,
            user_id=user_id,
            model=model,
            stream_id=t.conversation_stream_id,
            topic_id=body.topic_id,
            kind=body.kind,
            legal_excerpt=body.legal_excerpt,
        )
        audit(db, user_id=user_id, action="agent.document_chain.done", detail={})
        db.commit()
        return out
    except Exception as e:
        audit(db, user_id=user_id, action="agent.document_chain.fail", detail={"error": str(e)})
        db.commit()
        raise HTTPException(500, str(e)) from e


@router.post("/auth/simulation-chain")
def simulation_chain_auth(
    body: SimulationChainIn,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    t = get_owned_topic(db, body.topic_id, user_id)
    model = resolve_model(db, user_id=user_id, topic_session_id=body.topic_id, task="simulation")
    audit(db, user_id=user_id, action="agent.simulation_chain.start", detail={"topic_id": body.topic_id})
    db.commit()
    try:
        out = run_simulation_agent_chain(
            db,
            user_id=user_id,
            model=model,
            stream_id=t.conversation_stream_id,
            topic_id=body.topic_id,
            scenario_hint=(body.scenario_hint or "").strip(),
            legal_excerpt=body.legal_excerpt,
        )
        audit(db, user_id=user_id, action="agent.simulation_chain.done", detail={})
        db.commit()
        return out
    except Exception as e:
        audit(db, user_id=user_id, action="agent.simulation_chain.fail", detail={"error": str(e)})
        db.commit()
        raise HTTPException(500, str(e)) from e
