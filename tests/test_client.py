import copy
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from hyperodds import (
    APIConnectionError,
    APIError,
    Hyperodds,
    HyperoddsError,
    MarketDetail,
    MarketList,
    NotFoundError,
    RateLimitError,
)
from hyperodds._client import USER_AGENT, _Pacer

from .conftest import MakeClient, data, load, procedure, recorded, sent_input, text


def recording(requests: list[httpx.Request], answer: Any = recorded) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response: httpx.Response = answer(request) if callable(answer) else answer
        return response

    return handler


def test_list_sends_filters_under_the_api_names(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    ho = make_client(recording(requests))
    ho.markets.list(
        category="crypto",
        deployer="skew",
        q="BTC",
        sort="edge",
        settles_before=datetime(2026, 11, 6),
        min_edge_pts=1.5,
        min_volume=1000,
        min_depth_usd=250,
        limit=5,
        include_settled=True,
    )
    (request,) = requests
    assert request.method == "GET"
    assert request.url.path == "/trpc/markets.list"
    assert sent_input(request) == {
        "category": "crypto",
        "deployer": "skew",
        "q": "BTC",
        "sort": "edge",
        "settlesBefore": int(datetime(2026, 11, 6, tzinfo=timezone.utc).timestamp() * 1000),
        "minEdgePts": 1.5,
        "minVolume": 1000,
        "minDepthUsd": 250,
        "limit": 5,
        "includeSettled": True,
    }


def test_list_without_filters_sends_no_input(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    markets = make_client(recording(requests)).markets.list()
    assert "input" not in requests[0].url.params
    assert isinstance(markets, MarketList)
    assert len(markets) == len(data("markets.list.json"))


def test_list_reads_every_market(make_client: MakeClient) -> None:
    markets = make_client().markets.list()
    raw = data("markets.list.json")
    for market, row in zip(markets, raw, strict=True):
        assert market.outcome == row["outcome"]
        assert market.volume_24h == row["volume24h"]
        assert market.side_labels == tuple(row["sideLabels"])
        if row["eventTime"] is not None:
            assert market.event_time is not None
            assert market.event_time.tzinfo is timezone.utc
            assert market.event_time.timestamp() * 1000 == pytest.approx(row["eventTime"])
        assert market.url == f"https://usehyperodds.com/m/{row['outcome']}"


def test_fields_the_api_adds_later_are_kept(make_client: MakeClient) -> None:
    body = copy.deepcopy(load("markets.list.json"))
    body["result"]["data"][0]["newField"] = {"a": 1}
    (market, *_) = make_client(lambda _: httpx.Response(200, json=body)).markets.list()
    assert market.model_extra == {"newField": {"a": 1}}


def test_get_reads_one_market_in_full(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    detail = make_client(recording(requests)).markets.get(24994)
    assert sent_input(requests[0]) == {"outcome": 24994}
    raw = data("markets.get.matched.json")
    assert detail.outcome == raw["summary"]["outcome"]
    assert detail.title == raw["summary"]["title"]
    assert detail.book is not None
    assert detail.book.bids[0].px == raw["book"]["bids"][0]["px"]
    assert [m.venue for m in detail.matches] == [m["venue"] for m in raw["matches"]]
    pm = next(m for m in detail.matches if m.venue == "pm")
    assert pm.mid == pytest.approx((pm.bid + pm.ask) / 2)  # type: ignore[operator]


def test_get_takes_numpy_style_ints(make_client: MakeClient, requests: list[httpx.Request]) -> None:
    class Int64(int):
        pass

    make_client(recording(requests)).markets.get(Int64(24994))
    assert requests[0].url.params["input"] == '{"outcome":24994}'


def test_numbers_from_pandas_are_sent_as_plain_json(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    np = pytest.importorskip("numpy")
    ho = make_client(recording(requests))
    ho.markets.list(limit=np.int64(5), min_volume=np.float64(1000.5))
    ho.quotes.compare(np.int64(24994), side=np.int64(1), action="buy", shares=np.int64(100))
    assert sent_input(requests[0]) == {"limit": 5, "minVolume": 1000.5}
    assert sent_input(requests[1]) == {"outcome": 24994, "side": 1, "action": "buy", "shares": 100}


def test_an_id_that_is_not_a_whole_number_is_refused(make_client: MakeClient) -> None:
    with pytest.raises(TypeError, match="outcome must be a whole number"):
        make_client().markets.get(24994.9)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "ms"),
    [
        (datetime(2026, 11, 23, 12, tzinfo=timezone.utc), 1795435200000),
        (datetime(2026, 11, 23, 12), 1795435200000),
        (date(2026, 11, 23), 1795392000000),
        (1795435200000, 1795435200000),
        (1795435200000.0, 1795435200000),
    ],
)
def test_settles_before_takes_datetimes_dates_and_milliseconds(
    make_client: MakeClient, requests: list[httpx.Request], value: Any, ms: int
) -> None:
    make_client(recording(requests)).markets.list(settles_before=value)
    assert sent_input(requests[0]) == {"settlesBefore": ms}


def test_settles_before_takes_numpy_datetimes(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    np = pytest.importorskip("numpy")
    make_client(recording(requests)).markets.list(settles_before=np.datetime64("2026-11-23T12:00"))
    assert sent_input(requests[0]) == {"settlesBefore": 1795435200000}


@pytest.mark.parametrize(
    "call",
    [
        lambda ho: ho.markets.list(min_edge_pts=float("nan")),
        lambda ho: ho.markets.list(min_volume=float("inf")),
        lambda ho: ho.markets.list(min_depth_usd=-float("inf")),
        lambda ho: ho.markets.edges(min_confidence=float("nan")),
        lambda ho: ho.quotes.compare(1, side=0, action="buy", shares=float("inf")),
        lambda ho: ho.markets.list(settles_before=float("nan")),
    ],
)
def test_nan_and_infinity_are_refused_before_sending(
    make_client: MakeClient, requests: list[httpx.Request], call: Any
) -> None:
    with pytest.raises(ValueError, match="must be a finite number"):
        call(make_client(recording(requests)))
    assert requests == []


def test_a_query_with_nan_is_refused_before_sending(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    with pytest.raises(ValueError, match="NaN and infinity"):
        make_client(recording(requests)).query("markets.list", {"minVolume": float("nan")})
    assert requests == []


@pytest.mark.parametrize("value", [0, -1, -1.5, datetime(1960, 1, 1, tzinfo=timezone.utc)])
def test_settles_before_at_or_before_1970_is_refused(make_client: MakeClient, value: Any) -> None:
    with pytest.raises(ValueError, match="must be after 1970-01-01"):
        make_client().markets.list(settles_before=value)


@pytest.mark.parametrize("value", [True, "24994", b"24994"])
def test_a_string_or_a_bool_is_not_an_id(make_client: MakeClient, value: Any) -> None:
    with pytest.raises(TypeError, match="outcome must be a whole number"):
        make_client().markets.get(value)
    with pytest.raises(TypeError, match="question must be a whole number"):
        make_client().questions.get(value)


@pytest.mark.parametrize(
    ("value", "error"),
    [(True, TypeError), ("abc", ValueError), ("-5", ValueError), ("1" * 16, ValueError)],
)
def test_listings_refuse_an_event_id_the_api_never_sends(
    make_client: MakeClient, value: Any, error: type[Exception]
) -> None:
    with pytest.raises(error, match="last_event_id"):
        next(make_client().live.listings(last_event_id=value))


def test_a_closed_client_says_so(make_client: MakeClient) -> None:
    with make_client() as ho:
        pass
    with pytest.raises(HyperoddsError, match="closed"):
        ho.markets.facets()
    with pytest.raises(HyperoddsError, match="closed"):
        next(ho.live.listings(last_event_id=1))


def test_settles_before_in_seconds_is_refused(make_client: MakeClient) -> None:
    with pytest.raises(ValueError, match="looks like seconds"):
        make_client().markets.list(settles_before=1795435200)
    with pytest.raises(TypeError, match="takes a datetime"):
        make_client().markets.list(settles_before="2026-11-23")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"base_url": "api.usehyperodds.com"}, "base_url needs https://"),
        ({"base_url": "ftp://api.usehyperodds.com"}, "base_url needs https://"),
        ({"rate_limit": 0}, "rate_limit"),
        ({"rate_limit": -5}, "rate_limit"),
        ({"max_retries": -1}, "max_retries"),
        ({"timeout": 0}, "timeout"),
    ],
)
def test_options_that_cannot_work_are_refused(options: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Hyperodds(**options)


def test_follows_a_redirect_to_https() -> None:
    with Hyperodds("http://api.usehyperodds.com") as ho:
        assert ho._http.follow_redirects


def test_unknown_market_raises_not_found(make_client: MakeClient) -> None:
    missing = load("error.not-found.json")
    ho = make_client(lambda _: httpx.Response(missing["status"], json=missing["body"]))
    with pytest.raises(NotFoundError) as caught:
        ho.markets.get(1)
    err = caught.value
    assert err.status == 404
    assert err.code == "NOT_FOUND"
    assert err.procedure == "markets.get"
    assert str(err) == "404 No such market. (markets.get)"


def test_get_many_replays_a_recorded_batch(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    batch = load("markets.get.batch.json")
    ho = make_client(recording(requests, httpx.Response(batch["status"], json=batch["body"])))
    details = ho.markets.get_many(batch["outcomes"])
    found = [o for o in batch["outcomes"] if o != 1]
    assert list(details) == found
    assert all(isinstance(d, MarketDetail) for d in details.values())
    (request,) = requests
    assert procedure(request) == "markets.get,markets.get,markets.get"
    assert request.url.params["batch"] == "1"
    assert sent_input(request) == {str(i): {"outcome": o} for i, o in enumerate(batch["outcomes"])}


def batch_answer(request: httpx.Request) -> httpx.Response:
    """Answer a markets.get batch, with outcomes divisible by 7 missing."""
    template = data("markets.get.matched.json")
    missing = load("error.not-found.json")["body"]
    entries = []
    for call in sent_input(request).values():
        if call["outcome"] % 7 == 0:
            entries.append(missing)
            continue
        detail = copy.deepcopy(template)
        detail["summary"]["outcome"] = call["outcome"]
        entries.append({"result": {"data": detail}})
    return httpx.Response(207, json=entries)


def test_get_many_sends_ten_calls_a_request_and_skips_missing_markets(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    outcomes = [*range(1, 24), 3, 5]
    details = make_client(recording(requests, batch_answer)).markets.get_many(outcomes)

    assert [len(sent_input(r)) for r in requests] == [10, 10, 3]
    assert list(details) == [o for o in range(1, 24) if o % 7]
    assert all(details[o].outcome == o for o in details)


def flaky_batch(failures: dict[int, int]) -> Any:
    """Answer markets.get batches; each outcome in ``failures`` fails with a 500 that many times."""
    template = data("markets.get.matched.json")
    broken = {
        "error": {
            "message": "Something went wrong on our side. Try again in a minute.",
            "code": -32603,
            "data": {"code": "INTERNAL_SERVER_ERROR", "httpStatus": 500, "path": "markets.get"},
        }
    }

    def answer(request: httpx.Request) -> httpx.Response:
        entries = []
        for call in sent_input(request).values():
            if failures.get(call["outcome"], 0) > 0:
                failures[call["outcome"]] -= 1
                entries.append(broken)
                continue
            detail = copy.deepcopy(template)
            detail["summary"]["outcome"] = call["outcome"]
            entries.append({"result": {"data": detail}})
        return httpx.Response(207, json=entries)

    return answer


def test_get_many_reads_a_failed_market_once_more(
    make_client: MakeClient, requests: list[httpx.Request], sleeps: list[float]
) -> None:
    ho = make_client(recording(requests, flaky_batch({4: 1})))
    details = ho.markets.get_many(range(1, 13))
    assert list(details) == list(range(1, 13))
    assert [list(sent_input(r).values()) for r in requests][-1] == [{"outcome": 4}]
    assert len(sleeps) == 1


def test_get_many_reads_every_batch_before_raising(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    ho = make_client(recording(requests, flaky_batch({4: 2})))
    with pytest.raises(APIError) as caught:
        ho.markets.get_many(range(1, 13))
    assert caught.value.status == 500
    assert [len(sent_input(r)) for r in requests] == [10, 2, 1]


def test_get_many_raises_errors_other_than_not_found(make_client: MakeClient) -> None:
    refused = {
        "error": {
            "message": "Too small: expected number to be >=0 (outcome)",
            "code": -32600,
            "data": {"code": "BAD_REQUEST", "httpStatus": 400, "path": "markets.get"},
        }
    }
    ho = make_client(lambda _: httpx.Response(400, json=[refused]))
    with pytest.raises(APIError) as caught:
        ho.markets.get_many([-1])
    assert caught.value.status == 400
    assert caught.value.code == "BAD_REQUEST"


def test_get_many_of_nothing_sends_nothing(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    assert make_client(recording(requests)).markets.get_many([]) == {}
    assert requests == []


def test_waits_as_long_as_a_429_asks(make_client: MakeClient, sleeps: list[float]) -> None:
    busy = {"error": {"message": "Too many requests. Try again in a second."}}
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "2"}, json=busy)
        return recorded(request)

    assert make_client(handler).markets.facets().total > 0
    assert sleeps == [2.0]


def test_retries_while_markets_load(make_client: MakeClient, sleeps: list[float]) -> None:
    loading = {"error": {"message": "Markets are still loading. Try again in a few seconds."}}
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json=loading) if calls < 3 else recorded(request)

    make_client(handler).meta.status()
    assert calls == 3
    assert len(sleeps) == 2
    assert 1.5 <= sleeps[0] <= 3
    assert 3 <= sleeps[1] <= 6


def test_three_retries_wait_out_a_restart(make_client: MakeClient, sleeps: list[float]) -> None:
    loading = {"error": {"message": "Markets are still loading. Try again in a few seconds."}}
    with pytest.raises(APIError) as caught:
        make_client(lambda _: httpx.Response(503, json=loading)).meta.status()
    assert caught.value.status == 503
    assert 10.5 <= sum(sleeps) <= 21


@pytest.mark.parametrize(
    ("status", "retried"),
    [(500, True), (520, True), (524, True), (501, False), (505, False), (404, False)],
)
def test_which_statuses_are_retried(
    make_client: MakeClient, sleeps: list[float], status: int, retried: bool
) -> None:
    with pytest.raises(APIError):
        make_client(lambda _: httpx.Response(status, text="origin error")).markets.list()
    assert len(sleeps) == (3 if retried else 0)


def test_gives_up_after_max_retries(make_client: MakeClient, sleeps: list[float]) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429, headers={"retry-after": "1"}, json={"error": {"message": "slow"}}
        )

    with pytest.raises(RateLimitError) as caught:
        make_client(handler, max_retries=2).markets.list()
    assert calls == 3
    assert sleeps == [1.0, 1.0]
    assert caught.value.retry_after == 1.0


@pytest.mark.parametrize(("retry_after", "level"), [("1", logging.INFO), ("10", logging.WARNING)])
def test_a_long_wait_is_a_warning(
    make_client: MakeClient, caplog: pytest.LogCaptureFixture, retry_after: str, level: int
) -> None:
    answers = iter([httpx.Response(429, headers={"retry-after": retry_after}, json={})])
    ho = make_client(lambda request: next(answers, None) or recorded(request))
    with caplog.at_level(logging.INFO, logger="hyperodds"):
        ho.markets.facets()
    (record,) = caplog.records
    assert record.levelno == level
    assert "markets.facets answered 429, retrying in" in record.getMessage()


def test_does_not_wait_out_a_long_retry_after(make_client: MakeClient, sleeps: list[float]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, headers={"retry-after": "3600"}, json={"error": {"message": "x"}}
        )

    with pytest.raises(RateLimitError) as caught:
        make_client(handler).markets.list()
    assert caught.value.retry_after == 3600
    assert sleeps == []


def test_does_not_retry_a_bad_request(make_client: MakeClient, sleeps: list[float]) -> None:
    body = {
        "error": {
            "message": 'Invalid option: expected one of "volume"|"edge"|"new"|"settling" (sort)',
            "code": -32600,
            "data": {"code": "BAD_REQUEST", "httpStatus": 400, "path": "markets.list"},
        }
    }
    with pytest.raises(APIError) as caught:
        make_client(lambda _: httpx.Response(400, json=body)).markets.list(sort="best")  # type: ignore[arg-type]
    assert caught.value.code == "BAD_REQUEST"
    assert sleeps == []


def test_a_network_failure_is_retried_then_named(
    make_client: MakeClient, sleeps: list[float]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(APIConnectionError, match=re.escape("Could not reach https://api.test")):
        make_client(handler, max_retries=2).markets.list()
    assert len(sleeps) == 2


def test_a_read_timeout_says_the_api_was_reached(make_client: MakeClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(APIConnectionError) as caught:
        make_client(handler).markets.list()
    assert str(caught.value).startswith(
        "Reached https://api.test, but it did not answer within 5 s"
    )


def test_a_proxy_failure_names_the_proxy(
    make_client: MakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@proxy.test:3128")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(APIConnectionError) as caught:
        make_client(handler).markets.list()
    message = str(caught.value)
    assert "through the proxy at http://proxy.test:3128" in message
    assert "secret" not in message


def test_a_batch_retry_names_the_procedure_once(
    make_client: MakeClient, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={}) if calls == 1 else batch_answer(request)

    with caplog.at_level(logging.INFO, logger="hyperodds"):
        make_client(handler).markets.get_many([1, 2, 3])
    (record,) = caplog.records
    assert record.getMessage().startswith("hyperodds: markets.get (3 calls) answered 503")


def test_an_error_page_that_is_not_json(make_client: MakeClient) -> None:
    page = httpx.Response(
        403,
        text="<html>\n  <title>Access denied</title>\n</html>",
        headers={"content-type": "text/html; charset=UTF-8", "cf-ray": "a5e8e8ecfd382448-CPH"},
    )
    ho = make_client(lambda _: page)
    with pytest.raises(APIError) as caught:
        ho.markets.list()
    assert not isinstance(caught.value, NotFoundError)
    assert str(caught.value) == (
        "403 Unexpected answer (text/html, cf-ray a5e8e8ecfd382448-CPH): "
        "'<html> <title>Access denied</title> </html>'"
    )


def test_a_success_without_a_result_is_an_error(make_client: MakeClient) -> None:
    ho = make_client(lambda _: httpx.Response(200, text="<html>Sign in to the network</html>"))
    with pytest.raises(APIError, match="Unexpected answer") as caught:
        ho.markets.list()
    assert caught.value.procedure == "markets.list"
    assert isinstance(caught.value.__cause__, TypeError)


def test_a_misspelled_procedure_is_not_a_missing_market(make_client: MakeClient) -> None:
    body = {
        "error": {
            "message": 'No procedure found on path "markets.nope"',
            "code": -32004,
            "data": {"code": "NOT_FOUND", "httpStatus": 404, "path": "markets.nope"},
        }
    }
    ho = make_client(lambda _: httpx.Response(404, json=body))
    with pytest.raises(APIError) as caught:
        ho.query("markets.nope")
    assert not isinstance(caught.value, NotFoundError)
    assert caught.value.code == "NOT_FOUND"


@pytest.mark.parametrize("answer", [[{"outcome": 1}], None, {"not": "a list"}])
def test_an_answer_the_models_cannot_read_is_an_api_error(
    make_client: MakeClient, answer: Any
) -> None:
    ho = make_client(lambda _: httpx.Response(200, json={"result": {"data": answer}}))
    with pytest.raises(APIError, match="does not fit hyperodds") as caught:
        ho.markets.list()
    assert caught.value.procedure == "markets.list"
    assert isinstance(caught.value.__cause__, ValidationError)
    assert "1 problems" not in str(caught.value)


def test_settlement_delays_can_be_unknown(make_client: MakeClient) -> None:
    body = copy.deepcopy(load("markets.list.json"))
    risk = body["result"]["data"][0]["settlementRisk"]
    risk["pastDelays"] = {"count": 0, "medianMs": None, "maxMs": None}
    (market, *_) = make_client(lambda _: httpx.Response(200, json=body)).markets.list()
    assert market.settlement_risk.past_delays is not None
    assert market.settlement_risk.past_delays.median_ms is None


def test_pacer_lets_a_burst_through_then_spaces_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("hyperodds._client.time.monotonic", lambda: now[0])
    monkeypatch.setattr("hyperodds._client.time.sleep", sleep)

    pacer = _Pacer(rate=2)
    for _ in range(6):
        pacer.take()
    assert slept == []
    pacer.take()
    assert slept == [pytest.approx(0.5)]
    now[0] += 10
    pacer.take(6)
    assert len(slept) == 1


def test_each_call_in_a_batch_counts_against_the_rate(make_client: MakeClient) -> None:
    taken: list[int] = []

    class Spy:
        def take(self, n: int = 1) -> None:
            taken.append(n)

    ho = make_client(batch_answer)
    ho._pacer = Spy()  # type: ignore[assignment]
    ho.markets.get_many(range(1, 13))
    assert taken == [10, 2]


def test_base_url_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYPERODDS_API_URL", "http://localhost:3110/")
    with Hyperodds() as ho:
        assert ho.base_url == "http://localhost:3110"
    with Hyperodds("https://example.test") as ho:
        assert ho.base_url == "https://example.test"


def test_says_who_is_calling(make_client: MakeClient, requests: list[httpx.Request]) -> None:
    make_client(recording(requests)).meta.status()
    assert requests[0].headers["user-agent"] == USER_AGENT
    assert USER_AGENT.startswith("hyperodds-python/")


def test_query_calls_any_procedure_by_name(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    result = make_client(recording(requests)).query("markets.facets")
    assert result == data("markets.facets.json")


@pytest.mark.parametrize(
    ("call", "path", "expected_input"),
    [
        (lambda ho: ho.markets.newest(limit=3), "markets.newest", {"limit": 3}),
        (lambda ho: ho.markets.newest(), "markets.newest", None),
        (lambda ho: ho.markets.edges(limit=3, min_confidence=0.6), "markets.edges",
         {"limit": 3, "minConfidence": 0.6}),
        (lambda ho: ho.markets.book(24994, side=1), "markets.book", {"outcome": 24994, "side": 1}),
        (lambda ho: ho.markets.trades(24994, limit=5), "markets.trades",
         {"outcome": 24994, "limit": 5}),
        (lambda ho: ho.markets.facets(), "markets.facets", None),
        (lambda ho: ho.markets.history(24994), "markets.history", {"outcome": 24994}),
        (lambda ho: ho.markets.history(24994, since=datetime(2026, 11, 25, 6),
                                       until=datetime(2026, 11, 25, 18),
                                       every=timedelta(minutes=5)),
         "markets.history",
         {"outcome": 24994, "since": 1795586400000, "until": 1795629600000, "every": 300000}),
        (lambda ho: ho.markets.history(24994, every=60_000), "markets.history",
         {"outcome": 24994, "every": 60000}),
        (lambda ho: ho.markets.venue_history(24994, date(2026, 11, 20)), "markets.venueHistory",
         {"outcome": 24994, "from": 1795132800000, "step": 60}),
        (lambda ho: ho.markets.venue_history(24994, 1795132800000, step=240),
         "markets.venueHistory", {"outcome": 24994, "from": 1795132800000, "step": 240}),
        (lambda ho: ho.questions.list(), "questions.list", None),
        (lambda ho: ho.questions.get(580), "questions.get", {"question": 580}),
        (lambda ho: ho.clusters.list(), "clusters.list", None),
        (lambda ho: ho.clusters.get("abc"), "clusters.get", {"id": "abc"}),
        (lambda ho: ho.quotes.compare(24994, side=0, action="buy", shares=100), "quotes.compare",
         {"outcome": 24994, "side": 0, "action": "buy", "shares": 100}),
        (lambda ho: ho.meta.status(), "meta.status", None),
    ],
)  # fmt: skip
def test_each_procedure_sends_its_input_and_reads_its_answer(
    make_client: MakeClient,
    requests: list[httpx.Request],
    call: Any,
    path: str,
    expected_input: Any,
) -> None:
    result = call(make_client(recording(requests)))
    assert result is not None
    (request,) = requests
    assert procedure(request) == path
    assert sent_input(request) == expected_input


def test_a_side_1_book_says_which_side_it_prices(make_client: MakeClient) -> None:
    book = make_client().markets.book(24994, side=1)
    assert book is not None
    assert book.side == 1
    assert (book.to_dataframe().book_side == 1).all()
    assert "side" not in book.model_dump(by_alias=True)


def test_a_book_that_has_not_loaded_is_none(make_client: MakeClient) -> None:
    ho = make_client(lambda _: httpx.Response(200, json={"result": {"data": None}}))
    assert ho.markets.book(23295) is None


def sse(*events: str) -> bytes:
    return "".join(e + "\n\n" for e in events).encode()


def test_listings_yield_new_markets_and_resume_where_they_left_off(
    make_client: MakeClient, requests: list[httpx.Request], sleeps: list[float]
) -> None:
    recorded_stream = text("live.listings.sse").encode()
    first_id = next(
        line[4:] for line in recorded_stream.decode().splitlines() if line.startswith("id: ")
    )
    stop = sse(
        'event: serialized-error\ndata: {"message":"Use an event id this stream sent '
        '(lastEventId).","code":-32600,"data":{"code":"BAD_REQUEST","httpStatus":400,'
        '"path":"live.listings"}}'
    )
    streams = iter([recorded_stream, stop])

    def answer(request: httpx.Request) -> httpx.Response:
        if procedure(request) == "markets.newest":
            return recorded(request)
        assert request.headers["accept"] == "text/event-stream"
        return httpx.Response(200, content=next(streams))

    listings = make_client(recording(requests, answer)).live.listings()
    first = next(listings)
    assert isinstance(first, MarketList)
    assert [m.outcome for m in first] == [
        m["outcome"] for m in json.loads(_data_line(recorded_stream))
    ]
    with pytest.raises(APIError, match="Use an event id"):
        next(listings)

    newest = data("markets.newest.json")[0]["outcome"]
    assert [procedure(r) for r in requests] == ["markets.newest", "live.listings", "live.listings"]
    assert sent_input(requests[1]) == {"lastEventId": str(newest)}
    assert sent_input(requests[2]) == {"lastEventId": first_id}
    assert sleeps == [1.0]


def test_listings_reconnect_after_a_dropped_connection(
    make_client: MakeClient, sleeps: list[float]
) -> None:
    calls = 0

    def answer(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadError("connection reset", request=request)
        return httpx.Response(200, content=text("live.listings.sse").encode())

    listings = make_client(answer).live.listings(last_event_id=25840)
    assert len(next(listings)) > 0
    listings.close()
    assert calls == 2
    assert len(sleeps) == 1


def test_listings_give_up_when_the_stream_cap_stays_full(
    make_client: MakeClient, sleeps: list[float]
) -> None:
    full = sse(
        'event: connected\ndata: {"reconnectAfterInactivityMs":45000}',
        'event: serialized-error\ndata: {"message":"Too many live streams are open from here. '
        'Close a tab and try again.","code":-32029,"data":{"code":"TOO_MANY_REQUESTS",'
        '"httpStatus":429,"path":"live.listings"}}',
    )
    ho = make_client(lambda _: httpx.Response(200, content=full), max_retries=2)
    with pytest.raises(RateLimitError, match="Too many live streams"):
        next(ho.live.listings(last_event_id=1))
    assert len(sleeps) == 2


def test_listings_keep_their_retries_across_healthy_reconnects(
    make_client: MakeClient, sleeps: list[float]
) -> None:
    quiet = sse(
        'event: connected\ndata: {"reconnectAfterInactivityMs":45000}', "event: ping\ndata: "
    )
    script = iter(["drop", "quiet"] * 5 + ["listing"])

    def answer(request: httpx.Request) -> httpx.Response:
        step = next(script)
        if step == "drop":
            raise httpx.ReadError("connection reset", request=request)
        if step == "quiet":
            return httpx.Response(200, content=quiet)
        return httpx.Response(200, content=text("live.listings.sse").encode())

    listings = make_client(answer, max_retries=3).live.listings(last_event_id=1)
    assert len(next(listings)) > 0
    listings.close()
    assert sleeps.count(1.0) == 5


def test_listings_wait_as_long_as_a_refused_stream_asks(
    make_client: MakeClient, sleeps: list[float]
) -> None:
    refused = sse(
        'event: serialized-error\ndata: {"message":"Too many requests. Try again in a second.",'
        '"code":-32029,"data":{"code":"TOO_MANY_REQUESTS","httpStatus":429,"path":"live.listings"}}'
    )
    ho = make_client(
        lambda _: httpx.Response(200, headers={"retry-after": "4"}, content=refused), max_retries=2
    )
    with pytest.raises(RateLimitError) as caught:
        next(ho.live.listings(last_event_id=1))
    assert caught.value.retry_after == 4.0
    assert sleeps == [4.0, 4.0]


def _data_line(stream: bytes) -> str:
    return next(line[6:] for line in stream.decode().splitlines() if line.startswith("data: ["))


def test_history_in_seconds_is_refused(make_client: MakeClient) -> None:
    with pytest.raises(ValueError, match="since takes milliseconds"):
        make_client().markets.history(24994, since=1795586400)


def test_venue_history_names_the_window_served(make_client: MakeClient) -> None:
    served = make_client().markets.venue_history(24994, datetime(2026, 11, 22, 20, 20))
    raw = data("markets.venueHistory.json")
    assert served.since == datetime.fromtimestamp(raw["from"] / 1000, tz=timezone.utc)
    assert served.step == raw["step"]
    assert {line.venue for line in served.lines} <= {"pm", "kx"}


BARS = load("rest/udf.history.json")


def bars_feed(requests: list[httpx.Request], *pages: Any) -> Any:
    answers = iter(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        answer = next(answers)
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)

    return handler


def page(times: list[int], px: float = 50.0) -> dict[str, Any]:
    n = len(times)
    return {"s": "ok", "t": times, "o": [px] * n, "h": [px] * n, "l": [px] * n, "c": [px] * n,
            "v": [1] * n}  # fmt: skip


def test_bars_ask_the_feed_for_one_side_of_a_market(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    ho = make_client(recording(requests))
    ho.charts.bars(1473, "4h", side=1, limit=12, end=datetime(2026, 11, 25, 20))
    (request,) = requests
    assert request.url.path == "/udf/history"
    assert dict(request.url.params) == {
        "symbol": "HIP4:14731",
        "resolution": "240",
        "to": "1795636800",
        "countback": "12",
    }


def test_bars_carry_prices_from_0_to_1_and_utc_times(make_client: MakeClient) -> None:
    bars = make_client().charts.bars(1473, "1d", limit=12)
    assert len(bars) == len(BARS["t"])
    first = bars[0]
    assert first.at == datetime.fromtimestamp(BARS["t"][0], tz=timezone.utc)
    assert first.open == pytest.approx(BARS["o"][0] / 100)
    assert first.volume == BARS["v"][0]
    assert all(0 <= b.low <= b.open <= b.high <= 1 and b.low <= b.close <= b.high for b in bars)
    assert [b.at for b in bars] == sorted(b.at for b in bars)


def test_bars_from_a_start_send_the_range(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    ho = make_client(recording(requests))
    ho.charts.bars(1473, "1h", start=datetime(2026, 11, 20), end=datetime(2026, 11, 21))
    assert dict(requests[0].url.params) == {
        "symbol": "HIP4:14730",
        "resolution": "60",
        "to": "1795219200",
        "from": "1795132800",
    }


def test_bars_end_now_when_no_end_is_given(
    make_client: MakeClient, requests: list[httpx.Request], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hyperodds._client.time.time", lambda: 1795637000.25)
    make_client(recording(requests)).charts.bars(1473, limit=1)
    assert requests[0].url.params["to"] == "1795637001"
    assert requests[0].url.params["resolution"] == "60"


def test_bars_read_back_through_full_pages(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    newest = list(range(600_000, 900_000, 60))
    older = list(range(300_000, 600_000, 60))
    oldest = [120, 180]
    ho = make_client(bars_feed(requests, page(newest), page(older), page(oldest)))
    bars = ho.charts.bars(1473, "1m", end=datetime.fromtimestamp(900_000, tz=timezone.utc))
    assert len(bars) == 10_002
    assert [int(b.at.timestamp()) for b in bars] == oldest + older + newest
    assert [r.url.params["to"] for r in requests] == ["900000", "600000", "300000"]
    assert {r.url.params["countback"] for r in requests} == {"5000"}


def test_bars_stop_reading_once_the_limit_is_met(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    newest = list(range(600_000, 900_000, 60))
    older = list(range(480_000, 600_000, 60))
    ho = make_client(bars_feed(requests, page(newest), page(older)))
    bars = ho.charts.bars(1473, "1m", limit=7000)
    assert len(bars) == 7000
    assert [r.url.params["countback"] for r in requests] == ["5000", "2000"]


def test_a_market_that_never_traded_has_no_bars(make_client: MakeClient) -> None:
    bars = make_client(lambda _: httpx.Response(200, json={"s": "no_data"})).charts.bars(1473)
    assert bars == []
    assert bars.to_dataframe().empty


def day(year: int, month: int, d: int) -> int:
    return int(datetime(year, month, d, tzinfo=timezone.utc).timestamp())


def test_weeks_and_months_are_built_from_daily_bars(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    # Friday 30 October to Tuesday 3 November 2026: two weeks, two months.
    days = {
        "s": "ok",
        "t": [day(2026, 10, 30), day(2026, 11, 1), day(2026, 11, 2), day(2026, 11, 3)],
        "o": [10.0, 20.0, 30.0, 40.0],
        "h": [15.0, 25.0, 35.0, 45.0],
        "l": [5.0, 18.0, 28.0, 38.0],
        "c": [12.0, 22.0, 32.0, 42.0],
        "v": [1, 2, 3, 4],
    }
    ho = make_client(bars_feed(requests, days, days))
    weeks = ho.charts.bars(1473, "1w")
    assert [(b.at.date(), b.open, b.high, b.low, b.close, b.volume) for b in weeks] == [
        (date(2026, 10, 26), 0.10, 0.25, 0.05, 0.22, 3),
        (date(2026, 11, 2), 0.30, 0.45, 0.28, 0.42, 7),
    ]
    months = ho.charts.bars(1473, "1M", limit=1)
    assert [(b.at.date(), b.open, b.close, b.volume) for b in months] == [
        (date(2026, 11, 1), 0.20, 0.42, 9)
    ]
    assert [r.url.params["resolution"] for r in requests] == ["1D", "1D"]
    assert [r.url.params["countback"] for r in requests] == ["5000", "31"]


def test_a_week_starts_whole(make_client: MakeClient, requests: list[httpx.Request]) -> None:
    ho = make_client(recording(requests))
    ho.charts.bars(1473, "1w", start=datetime(2026, 11, 19, 15), end=datetime(2026, 11, 25))
    assert requests[0].url.params["from"] == str(day(2026, 11, 16))
    ho.charts.bars(1473, "1M", start=datetime(2026, 11, 19, 15), end=datetime(2026, 11, 25))
    assert requests[1].url.params["from"] == str(day(2026, 11, 1))


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"interval": "2h"}, "interval is one of 1m, 5m"),
        ({"limit": 0}, "limit is a number of bars"),
        ({"side": 2}, "side is 0 or 1"),
        ({"start": datetime(2026, 11, 25), "end": datetime(2026, 11, 24)}, "start must be before"),
        ({"start": 1795132800}, "start takes milliseconds"),
    ],
)
def test_bars_that_cannot_be_read_are_refused(
    make_client: MakeClient, requests: list[httpx.Request], options: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        make_client(recording(requests)).charts.bars(1473, **options)
    assert requests == []


def test_an_unknown_market_has_no_chart(make_client: MakeClient) -> None:
    missing = httpx.Response(404, json={"s": "error", "errmsg": "Unknown symbol."})
    with pytest.raises(NotFoundError) as caught:
        make_client(lambda _: missing).charts.bars(1)
    assert caught.value.message == "Unknown symbol."
    assert caught.value.procedure == "udf/history"


def test_bars_wait_while_the_feed_is_busy(
    make_client: MakeClient, requests: list[httpx.Request], sleeps: list[float]
) -> None:
    busy = httpx.Response(
        503,
        headers={"retry-after": "5"},
        json={"s": "error", "errmsg": "History is unavailable right now. Try again shortly."},
    )
    bars = make_client(bars_feed(requests, busy, BARS)).charts.bars(1473, "1d", limit=12)
    assert len(bars) == 12
    assert sleeps == [5.0]


@pytest.mark.parametrize(
    "answer",
    [{"s": "ok", "t": [1], "o": [1.0]}, {"s": "error", "errmsg": "x"}, ["bars"], None,
     {"s": "ok", "t": [1, 2], "o": [1], "h": [1], "l": [1], "c": [1], "v": [1]}],
)  # fmt: skip
def test_a_feed_answer_that_cannot_be_read_is_an_api_error(
    make_client: MakeClient, answer: Any
) -> None:
    with pytest.raises(APIError, match="chart feed's answer does not fit") as caught:
        make_client(lambda _: httpx.Response(200, json=answer)).charts.bars(1473)
    assert caught.value.procedure == "udf/history"


def test_sheets_send_columns_and_filters(
    make_client: MakeClient, requests: list[httpx.Request]
) -> None:
    ho = make_client(recording(requests))
    sheet = ho.sheets.markets(
        columns=["outcome", "mid"], category="crypto", deployer="Outcome", q="btc", sort="edge",
        limit=5, offset=10,
    )  # fmt: skip
    (request,) = requests
    assert request.url.path == "/sheets/markets.csv"
    assert request.headers["accept"] == "text/csv"
    assert dict(request.url.params) == {
        "columns": "outcome,mid",
        "category": "crypto",
        "deployer": "Outcome",
        "q": "btc",
        "sort": "edge",
        "limit": "5",
        "offset": "10",
    }
    assert sheet == text("rest/sheets.markets.csv")
    assert sheet.header


@pytest.mark.parametrize(
    ("call", "path", "params"),
    [
        (lambda ho: ho.sheets.markets(), "/sheets/markets.csv", {}),
        (lambda ho: ho.sheets.edges(limit=3), "/sheets/edges.csv", {"limit": "3"}),
        (lambda ho: ho.sheets.market(1473, columns=["mid"], header=False),
         "/sheets/markets/1473.csv", {"columns": "mid", "header": "0"}),
        (lambda ho: ho.sheets.question(430), "/sheets/questions/430.csv", {}),
    ],
)  # fmt: skip
def test_each_sheet_has_its_path(
    make_client: MakeClient, requests: list[httpx.Request], call: Any, path: str, params: Any
) -> None:
    csv = httpx.Response(200, text="0.727735\n", headers={"content-type": "text/csv"})
    sheet = call(make_client(recording(requests, csv)))
    assert sheet == "0.727735\n"
    assert requests[0].url.path == path
    assert dict(requests[0].url.params) == params


def test_a_sheet_remembers_whether_it_has_a_header(make_client: MakeClient) -> None:
    csv = httpx.Response(200, text="0.727735\n", headers={"content-type": "text/csv"})
    sheet = make_client(lambda _: csv).sheets.market(1473, columns=["mid"], header=False)
    assert not sheet.header
    assert float(sheet) == 0.727735


def test_columns_are_a_list(make_client: MakeClient) -> None:
    with pytest.raises(TypeError, match="columns is a list of names"):
        make_client().sheets.markets(columns="mid")


@pytest.mark.parametrize(
    ("status", "message", "error"),
    [
        (404, "No market with outcome id 1.", NotFoundError),
        (400, 'There\'s no column called "nope". Use: outcome, title.', APIError),
    ],
)
def test_a_sheet_refusal_carries_the_sentence(
    make_client: MakeClient, status: int, message: str, error: type[APIError]
) -> None:
    refusal = httpx.Response(
        status, text=message, headers={"content-type": "text/plain; charset=utf-8"}
    )
    with pytest.raises(error) as caught:
        make_client(lambda _: refusal).sheets.market(1)
    assert type(caught.value) is error
    assert caught.value.message == message
    assert caught.value.status == status
    assert caught.value.procedure == "sheets/markets/1.csv"


def test_a_sheet_waits_as_long_as_a_429_asks(
    make_client: MakeClient, requests: list[httpx.Request], sleeps: list[float]
) -> None:
    busy = httpx.Response(
        429,
        text="Too many requests. Try again in a second.",
        headers={"content-type": "text/plain; charset=utf-8", "retry-after": "1"},
    )
    handler = bars_feed(requests, busy, busy, busy, busy)
    with pytest.raises(RateLimitError) as caught:
        make_client(handler).sheets.edges()
    assert caught.value.retry_after == 1.0
    assert sleeps == [1.0, 1.0, 1.0]


def test_the_forecast_record_reads(make_client: MakeClient, requests: list[httpx.Request]) -> None:
    ho = make_client(recording(requests))
    board = ho.forecasts.board(limit=2, offset=4)
    assert board.records == 2
    assert [r.rank for r in board.rows] == [1, 2]
    days = ho.forecasts.records()
    assert [str(d.day) for d in days] == ["2025-11-22", "2025-11-21"]
    lines = ho.forecasts.day(days[0].day)
    assert [f.id for f in lines] == [101, 102]
    assert ho.forecasts.day("2025-11-22") == lines
    assert [(r.url.path, dict(r.url.params)) for r in requests] == [
        ("/forecasts/board", {"limit": "2", "offset": "4"}),
        ("/forecasts/records", {}),
        ("/forecasts/records/2025-11-22.jsonl", {}),
        ("/forecasts/records/2025-11-22.jsonl", {}),
    ]


def test_a_day_without_a_record_is_not_found(make_client: MakeClient) -> None:
    missing = httpx.Response(
        404, json={"error": "There is no record for this day.", "code": "not_found"}
    )
    with pytest.raises(NotFoundError) as caught:
        make_client(lambda _: missing).forecasts.day(date(2026, 11, 24))
    assert caught.value.message == "There is no record for this day."
    assert caught.value.code == "not_found"
    assert caught.value.procedure == "forecasts/records/2026-11-24.jsonl"


@pytest.mark.parametrize("value", ["yesterday", "2026-11-24.json", "../board", ""])
def test_a_day_is_a_date(
    make_client: MakeClient, requests: list[httpx.Request], value: str
) -> None:
    with pytest.raises(ValueError, match="day is a date or YYYY-MM-DD"):
        make_client(recording(requests)).forecasts.day(value)
    assert requests == []


def test_a_record_that_is_not_json_lines_is_an_api_error(make_client: MakeClient) -> None:
    page = httpx.Response(200, text="<html>not the record</html>")
    with pytest.raises(APIError, match="Unexpected answer"):
        make_client(lambda _: page).forecasts.day("2025-11-22")


def test_an_index_without_days_is_an_api_error(make_client: MakeClient) -> None:
    with pytest.raises(APIError, match="does not fit hyperodds"):
        make_client(lambda _: httpx.Response(200, json={"dias": []})).forecasts.records()
