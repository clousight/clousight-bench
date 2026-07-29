import json

from clousight_bench.core.sampling import HighFreqSampler


def test_sampler_emits_sample_lines(capsys):
    values = iter([10.0, 11.0, 12.0])
    sampler = HighFreqSampler(series_name="latency_ms", interval_s=0)
    sampler.collect(lambda: next(values), count=3)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    events = [json.loads(line) for line in lines]
    assert all(e["type"] == "sample" and e["series"] == "latency_ms" for e in events)
    assert [e["value"] for e in events] == [10.0, 11.0, 12.0]
    assert all("t" in e for e in events)


def test_sampler_coerces_to_float(capsys):
    sampler = HighFreqSampler(series_name="gpu_util", interval_s=0)
    sampler.collect(lambda: 42, count=1)
    event = json.loads(capsys.readouterr().out.strip())
    assert event["value"] == 42.0
    assert isinstance(event["value"], float)
