"""VALIDATE rejects a malformed request before any resource is touched."""

from collections.abc import Mapping

import pytest

from clousight_bench.core.errors import UserInputError
from clousight_bench.core.schema import RunSpec
from clousight_bench.core.validation import InvalidRunSpecError, validate_run_spec
from clousight_bench.domains.agent_runtime.tasks.t1_3_fault_recovery import (
    FaultRecoveryTask,
)


def test_a_well_formed_spec_validates():
    validate_run_spec(
        RunSpec("agent-runtime", "T1.3", "local-sim"), FaultRecoveryTask()
    )


def test_invalid_run_spec_error_is_a_user_input_error():
    assert issubclass(InvalidRunSpecError, UserInputError)


@pytest.mark.parametrize("field", ["domain", "task_id", "platform"])
@pytest.mark.parametrize("value", ["", "   ", None, 7])
def test_invalid_identifiers_are_rejected(field, value):
    spec = RunSpec("agent-runtime", "T1.3", "local-sim")
    setattr(spec, field, value)
    with pytest.raises(InvalidRunSpecError, match=field):
        validate_run_spec(spec, FaultRecoveryTask())


@pytest.mark.parametrize(
    ("field", "value"),
    [("target", ["not", "a", "mapping"]), ("params", "nope")],
)
def test_non_mapping_target_and_params_are_rejected(field, value):
    spec = RunSpec("agent-runtime", "T1.3", "local-sim")
    setattr(spec, field, value)
    with pytest.raises(InvalidRunSpecError, match=field):
        validate_run_spec(spec, FaultRecoveryTask())


def test_mapping_subclasses_are_accepted():
    class _Mapping(Mapping):
        def __init__(self, values):
            self._values = values

        def __getitem__(self, key):
            return self._values[key]

        def __iter__(self):
            return iter(self._values)

        def __len__(self):
            return len(self._values)

    spec = RunSpec("agent-runtime", "T1.3", "local-sim")
    spec.target = _Mapping({"region": "local"})
    spec.params = _Mapping({})
    validate_run_spec(spec, FaultRecoveryTask())


@pytest.mark.parametrize("field", ["target", "params"])
def test_non_finite_numbers_are_rejected_before_the_run(field):
    spec = RunSpec("agent-runtime", "T1.3", "local-sim")
    setattr(spec, field, {"budget": float("inf")})
    with pytest.raises(InvalidRunSpecError, match=field):
        validate_run_spec(spec, FaultRecoveryTask())


def test_secret_values_are_redacted_before_canonical_validation():
    spec = RunSpec(
        "agent-runtime",
        "T1.3",
        "local-sim",
        params={"api_token": object()},
    )
    validate_run_spec(spec, FaultRecoveryTask())


def test_a_task_that_cannot_describe_its_config_is_a_user_error():
    class _BadConfig(FaultRecoveryTask):
        def config(self, params):
            raise KeyError("missing-required-param")

    with pytest.raises(InvalidRunSpecError, match="config"):
        validate_run_spec(RunSpec("agent-runtime", "T1.3", "local-sim"), _BadConfig())


def test_task_config_must_be_a_mapping():
    class _BadConfig(FaultRecoveryTask):
        def config(self, params):
            return ["not", "a", "mapping"]

    with pytest.raises(InvalidRunSpecError, match="config.*mapping"):
        validate_run_spec(RunSpec("agent-runtime", "T1.3", "local-sim"), _BadConfig())


def test_task_config_must_be_canonically_encodable():
    class _BadConfig(FaultRecoveryTask):
        def config(self, params):
            return {"budget": float("nan")}

    with pytest.raises(InvalidRunSpecError, match="task config"):
        validate_run_spec(RunSpec("agent-runtime", "T1.3", "local-sim"), _BadConfig())
