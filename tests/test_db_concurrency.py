import threading


class TestDbConcurrency:
    """Regression test for the 'database is locked' outage: a leaked DB
    connection holding an uncommitted write used to wedge every writer.
    The with db_conn() refactor guarantees connections are closed on all
    paths, and busy_timeout makes transient contention wait — so concurrent
    writes must never raise sqlite3.OperationalError."""

    def test_concurrent_writes_never_lock(self):
        from db.gateways import register_gateway, update_gateway_last_seen
        from db.requests import create_request
        from db.fleet import create_fleet_command, approve_fleet_command, upsert_fleet_result
        from db.windows import create_approved_window

        register_gateway("10.0.0.1", "gw1", "v1")
        register_gateway("10.0.0.2", "gw2", "v1")
        cid = create_fleet_command("uptime", ["10.0.0.1", "10.0.0.2"], "operator", "r", 180)
        approve_fleet_command(cid)

        errors = []
        err_lock = threading.Lock()

        def worker(n):
            try:
                for i in range(40):
                    if n % 4 == 0:
                        update_gateway_last_seen("10.0.0.1")
                    elif n % 4 == 1:
                        create_request("10.0.0.1", "cmd-%d-%d" % (n, i), status="pending", ttl=90)
                    elif n % 4 == 2:
                        upsert_fleet_result(cid, "10.0.0.1", "running")
                    else:
                        create_approved_window("10.0.0.2", "hostname", days_of_week=1, execution_time=1200)
            except Exception as e:
                with err_lock:
                    errors.append(repr(e))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], "DB write errors under concurrency: %s" % errors[:5]

    def test_concurrent_last_seen_updates(self):
        from db.gateways import register_gateway, update_gateway_last_seen, get_gateways

        register_gateway("10.0.0.9", "gw9", "v1")
        errors = []
        err_lock = threading.Lock()

        def worker():
            try:
                for _ in range(60):
                    update_gateway_last_seen("10.0.0.9")
            except Exception as e:
                with err_lock:
                    errors.append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], "last_seen writes failed under concurrency: %s" % errors[:5]
        gws = get_gateways()
        gw = next(g for g in gws if g["ip"] == "10.0.0.9")
        assert gw["last_seen"] > 0
