# scripts/full_power_check.py — 满血实证：Karate 真实执行 + Docker 现供真 MySQL 对拍
# 手册口径：Docker CLI=C:\Docker\bin\docker\docker.exe，daemon=tcp://127.0.0.1:2375（docker-vm 需已启动）
import json, os, sys, time

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)
sys.path.insert(0, os.path.join(TM, "examples", "red-packet"))
from testmind import core


def karate_check():
    from sut import serve
    srv, _sut = serve(db_path=":memory:", port=18101)
    ev = core.Evidence()
    try:
        r = core.run_karate(os.path.join(TM, "examples", "red-packet", "karate_smoke.feature"), ev)
    finally:
        srv.shutdown()
    r["evidence"] = ev.run_id
    return r


def mysql_check():
    prov = core.docker_provision("mysql:8", "testmind-mysql-check", 3306,
                                 env={"MYSQL_ROOT_PASSWORD": os.environ.get("TM_TEST_DB_PW", "tm-check-only")})
    if prov["status"] != "AVAILABLE":
        return {"status": "BLOCKED", "reason": prov.get("reason", "docker unavailable"),
                "hint": 'VM 启动: "C:/Program Files/Oracle/VirtualBox/VBoxManage.exe" startvm docker-vm --type headless'
                        ' + controlvm natpf1 "mysql,tcp,,3306,,3306"'}
    try:
        import pymysql
        conn = None
        for _ in range(30):                    # MySQL 冷启动等待（真实环境真实等待 ≠ 假通过）
            try:
                conn = pymysql.connect(host="127.0.0.1", port=3306, user="root",
                                       password=os.environ.get("TM_TEST_DB_PW", "tm-check-only"), autocommit=True)
                break
            except Exception:
                time.sleep(3)
        if conn is None:
            return {"status": "BLOCKED", "reason": "mysql not ready in 90s"}
        cur = conn.cursor()
        cur.execute("CREATE DATABASE IF NOT EXISTS tm_check")
        cur.execute("USE tm_check")
        cur.execute("CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY AUTO_INCREMENT, v INT NOT NULL)")
        dbc = core.DBCheck(conn, tables=("t",))
        before = dbc.snapshot()
        dbc.exec("INSERT INTO t(v) VALUES(5)")
        after = dbc.snapshot()
        diff = core.DBCheck.diff(before, after)
        ok = len(diff["t"]["added"]) == 1 and after["t"][-1]["v"] == 5
        return {"status": "PASS" if ok else "FAIL", "runner": "docker+mysql8+pymysql",
                "db_after": after["t"][-1], "provision": prov}
    finally:
        core.docker_release("testmind-mysql-check")     # create→use→destroy 闭环


if __name__ == "__main__":
    k, m = karate_check(), mysql_check()
    print(json.dumps({"karate": k, "mysql": m}, ensure_ascii=False, indent=1, default=str))
    sys.exit(0 if k["status"] == "PASS" and m["status"] in ("PASS", "BLOCKED") else 1)
