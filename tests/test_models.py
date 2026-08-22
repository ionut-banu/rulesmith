from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from rulesmith.models import (
    Dataset,
    Rule,
    RuleResult,
    Run,
    Upload,
    get_session_factory,
    init_db,
)


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    return get_session_factory(engine)


def test_dataset_created_and_read_back(session_factory):
    with session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()

        fetched = session.get(Dataset, dataset.id)
        assert fetched.id is not None
        assert fetched.name == "orders"
        assert isinstance(fetched.created_at, datetime)


def test_dataset_name_not_null(session_factory):
    with session_factory() as session:
        session.add(Dataset(name=None))
        with pytest.raises(IntegrityError):
            session.commit()


def test_rule_visible_through_dataset_relationship(session_factory):
    with session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()

        rule = Rule(dataset_id=dataset.id, name="not_null", expression="col is not None")
        session.add(rule)
        session.commit()

        session.refresh(dataset)
        assert len(dataset.rules) == 1
        assert dataset.rules[0].name == "not_null"


def test_upload_persists_and_reads_back(session_factory):
    with session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()

        upload = Upload(
            dataset_id=dataset.id,
            original_filename="orders.csv",
            stored_path="/data/orders.csv",
            format="csv",
        )
        session.add(upload)
        session.commit()

        fetched = session.get(Upload, upload.id)
        assert fetched.original_filename == "orders.csv"
        assert fetched.stored_path == "/data/orders.csv"
        assert fetched.format == "csv"
        assert isinstance(fetched.uploaded_at, datetime)


def test_run_persists_and_reads_back(session_factory):
    with session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()

        upload = Upload(
            dataset_id=dataset.id,
            original_filename="orders.csv",
            stored_path="/data/orders.csv",
            format="csv",
        )
        session.add(upload)
        session.commit()

        run = Run(dataset_id=dataset.id, upload_id=upload.id)
        session.add(run)
        session.commit()

        fetched = session.get(Run, run.id)
        assert fetched.dataset_id == dataset.id
        assert fetched.upload_id == upload.id
        assert isinstance(fetched.created_at, datetime)


def test_only_one_run_per_upload(session_factory):
    with session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()

        upload = Upload(
            dataset_id=dataset.id,
            original_filename="orders.csv",
            stored_path="/data/orders.csv",
            format="csv",
        )
        session.add(upload)
        session.commit()

        session.add(Run(dataset_id=dataset.id, upload_id=upload.id))
        session.commit()

        session.add(Run(dataset_id=dataset.id, upload_id=upload.id))
        with pytest.raises(IntegrityError):
            session.commit()


def test_rule_result_persists_and_reads_back(session_factory):
    with session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()

        upload = Upload(
            dataset_id=dataset.id,
            original_filename="orders.csv",
            stored_path="/data/orders.csv",
            format="csv",
        )
        session.add(upload)
        session.commit()

        run = Run(dataset_id=dataset.id, upload_id=upload.id)
        session.add(run)
        session.commit()

        rule = Rule(dataset_id=dataset.id, name="not_null", expression="col is not None")
        session.add(rule)
        session.commit()

        result = RuleResult(
            run_id=run.id,
            rule_id=rule.id,
            rule_name="not_null",
            verdict="fail",
            pass_count=8,
            fail_count=2,
            broken_reason=None,
        )
        session.add(result)
        session.commit()

        fetched = session.get(RuleResult, result.id)
        assert fetched.rule_name == "not_null"
        assert fetched.verdict == "fail"
        assert fetched.pass_count == 8
        assert fetched.fail_count == 2
        assert fetched.broken_reason is None


def test_deleting_rule_does_not_corrupt_rule_result(session_factory):
    with session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()

        upload = Upload(
            dataset_id=dataset.id,
            original_filename="orders.csv",
            stored_path="/data/orders.csv",
            format="csv",
        )
        session.add(upload)
        session.commit()

        run = Run(dataset_id=dataset.id, upload_id=upload.id)
        session.add(run)
        session.commit()

        rule = Rule(dataset_id=dataset.id, name="not_null", expression="col is not None")
        session.add(rule)
        session.commit()

        result = RuleResult(
            run_id=run.id,
            rule_id=rule.id,
            rule_name="not_null",
            verdict="pass",
            pass_count=10,
            fail_count=0,
        )
        session.add(result)
        session.commit()
        result_id = result.id

        session.delete(rule)
        session.commit()

        fetched = session.get(RuleResult, result_id)
        assert fetched is not None
        assert fetched.rule_id is None
        assert fetched.rule_name == "not_null"
        assert fetched.verdict == "pass"


def test_rule_with_missing_dataset_id_raises_integrity_error(session_factory):
    with session_factory() as session:
        session.add(Rule(dataset_id=999, name="bad", expression="1"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_upload_with_missing_dataset_id_raises_integrity_error(session_factory):
    with session_factory() as session:
        session.add(
            Upload(
                dataset_id=999,
                original_filename="x.csv",
                stored_path="/x.csv",
                format="csv",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_run_with_missing_dataset_id_raises_integrity_error(session_factory):
    with session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()

        upload = Upload(
            dataset_id=dataset.id,
            original_filename="orders.csv",
            stored_path="/data/orders.csv",
            format="csv",
        )
        session.add(upload)
        session.commit()

        session.add(Run(dataset_id=999, upload_id=upload.id))
        with pytest.raises(IntegrityError):
            session.commit()


def test_deleting_dataset_cascades_to_children(session_factory):
    with session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()

        rule = Rule(dataset_id=dataset.id, name="not_null", expression="col is not None")
        upload = Upload(
            dataset_id=dataset.id,
            original_filename="orders.csv",
            stored_path="/data/orders.csv",
            format="csv",
        )
        session.add_all([rule, upload])
        session.commit()

        run = Run(dataset_id=dataset.id, upload_id=upload.id)
        session.add(run)
        session.commit()

        result = RuleResult(
            run_id=run.id,
            rule_id=rule.id,
            rule_name="not_null",
            verdict="pass",
            pass_count=1,
            fail_count=0,
        )
        session.add(result)
        session.commit()

        dataset_id = dataset.id
        rule_id = rule.id
        upload_id = upload.id
        run_id = run.id
        result_id = result.id

        session.delete(dataset)
        session.commit()

        assert session.get(Dataset, dataset_id) is None
        assert session.get(Rule, rule_id) is None
        assert session.get(Upload, upload_id) is None
        assert session.get(Run, run_id) is None
        assert session.get(RuleResult, result_id) is None


def test_init_db_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    init_db(engine)

    with get_session_factory(engine)() as session:
        session.add(Dataset(name="orders"))
        session.commit()
        assert session.query(Dataset).count() == 1
