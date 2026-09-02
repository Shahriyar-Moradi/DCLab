from pydantic import ValidationError

from app.domain.opportunity import OpportunityCreate
from app.services.ingestion_service import ingest_opportunities_csv


def test_opportunity_schema_round_trip():
    created = OpportunityCreate(
        external_id="opp_schema",
        customer_id="cust_1",
        amount=12000,
        currency="AED",
        stage="proposal",
        source="inbound",
        owner_id="rep_1",
        converted=1,
    )
    dumped = created.model_dump()
    again = OpportunityCreate.model_validate(dumped)
    assert again.external_id == "opp_schema"
    assert again.amount == 12000


def test_negative_amount_rejected_by_schema():
    try:
        OpportunityCreate(
            external_id="opp_bad",
            customer_id="cust_1",
            amount=-50,
            stage="proposal",
            source="inbound",
            owner_id="rep_1",
        )
        raise AssertionError("expected ValidationError")
    except ValidationError as exc:
        assert "amount" in str(exc)


def test_ingest_collects_row_errors(db_session):
    csv = (
        "external_id,customer_id,amount,currency,stage,source,owner_id,created_at,converted\n"
        "opp_ok,cust_1,1000,AED,proposal,inbound,rep_1,2026-01-01,1\n"
        "opp_neg,cust_2,-10,AED,proposal,inbound,rep_1,2026-01-01,0\n"
        "opp_date,cust_3,1000,AED,proposal,inbound,rep_1,not-a-date,0\n"
    ).encode()
    from app.db.models import DEFAULT_WORKSPACE_ID

    result = ingest_opportunities_csv(
        db_session, csv, workspace_id=DEFAULT_WORKSPACE_ID
    )
    assert result.inserted == 1
    assert result.rejected == 2
    assert any("amount" in err.reason for err in result.errors)
    assert any("created_at" in err.reason for err in result.errors)
