from app.domain.errors import UpstreamTimeout
from app.infra.readiness import ReadinessCheck
from tests.fakes import FakeClock, FakeGateway


def test_a_successful_check_reports_no_error(gateway: FakeGateway, clock: FakeClock):
    assert ReadinessCheck(gateway, reuse_seconds=5, clock=clock).check() is None


def test_a_failed_check_reports_the_error_code(gateway: FakeGateway, clock: FakeClock):
    gateway.error = UpstreamTimeout("slow")
    assert ReadinessCheck(gateway, reuse_seconds=5, clock=clock).check() == "upstream_timeout"


def test_the_result_is_reused_until_it_is_stale(gateway: FakeGateway, clock: FakeClock):
    readiness = ReadinessCheck(gateway, reuse_seconds=5, clock=clock)
    readiness.check()
    clock.advance(4)
    readiness.check()
    assert gateway.calls["list_continents"] == 1
    clock.advance(1)
    readiness.check()
    assert gateway.calls["list_continents"] == 2


def test_a_failure_is_reused_too_then_recovery_is_seen(gateway: FakeGateway, clock: FakeClock):
    readiness = ReadinessCheck(gateway, reuse_seconds=5, clock=clock)
    gateway.error = UpstreamTimeout("slow")
    assert readiness.check() == "upstream_timeout"
    gateway.error = None
    # During an outage, a flood of probes must not turn into a flood of upstream calls.
    assert readiness.check() == "upstream_timeout"
    clock.advance(5)
    assert readiness.check() is None


def test_zero_reuse_checks_upstream_every_time(gateway: FakeGateway, clock: FakeClock):
    readiness = ReadinessCheck(gateway, reuse_seconds=0, clock=clock)
    readiness.check()
    readiness.check()
    assert gateway.calls["list_continents"] == 2
