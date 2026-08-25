#!/usr/bin/env python3
"""
Bidirectional Archy (oc-memory) reconciliation between this Mac's local
write-authoritative replica and swabby's memory-server pod.

Each side writes independently at all times (no single-primary replication).
This script unions the two by content_hash: cells present on only one side
get raw-inserted into the other (original created_at/updated_at preserved,
content_hash makes insertion idempotent). Safe to run repeatedly, safe to
run while both sides are actively being written to, and a no-op exit 0 if
the remote is unreachable — the whole point is each replica keeps operating
independently and this just catches the two up whenever it can reach both.
"""
import base64
import json
import subprocess
import sqlite3
import sys

LOCAL_DB = "/Users/ueckerts/.oc-memory/local-replica/memory.db"
SSH_HOST = "swabby"
NAMESPACE = "swabby-memory"
LABEL = "app=memory-server"
REMOTE_DB = "/data/memory.db"
SSH_TIMEOUT = 10

REMOTE_POD_CMD = (
    f"sudo -n kubectl get pod -n {NAMESPACE} -l {LABEL} "
    "-o jsonpath='{.items[0].metadata.name}'"
)


def log(msg):
    print(msg, flush=True)


def ssh(cmd, input_data=None, timeout=60):
    return subprocess.run(
        ["ssh", "-o", f"ConnectTimeout={SSH_TIMEOUT}", "-o", "BatchMode=yes", SSH_HOST, cmd],
        input=input_data,
        capture_output=True,
        timeout=timeout,
    )


def remote_pod_name():
    r = ssh(REMOTE_POD_CMD)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(r.stderr.decode().strip() or "no pod found")
    return r.stdout.decode().strip()


def remote_exec(pod, py_code, input_data=None):
    cmd = f"sudo -n kubectl exec -i -n {NAMESPACE} {pod} -- python3 -c {json.dumps(py_code)}"
    r = ssh(cmd, input_data=input_data, timeout=90)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode().strip())
    return r.stdout


DUMP_HASHES_PY = "import sqlite3; c=sqlite3.connect('%s'); [print(h) for (h,) in c.execute('select content_hash from mem_cells where content_hash is not null')]" % REMOTE_DB

DUMP_CELLS_PY = """
import sqlite3, json, base64, sys
keep = set(l.strip() for l in sys.stdin if l.strip())
c = sqlite3.connect('%s')
c.row_factory = sqlite3.Row
rows = c.execute('select * from mem_cells where content_hash in ({})'.format(','.join('?'*len(keep))), list(keep)).fetchall() if keep else []
out = []
for r in rows:
    d = dict(r)
    if d.get('embedding') is not None:
        d['embedding'] = base64.b64encode(d['embedding']).decode()
    out.append(d)
print(json.dumps(out))
""" % REMOTE_DB

APPLY_CELLS_PY = """
import sqlite3, json, base64, sys
cells = json.load(sys.stdin)
c = sqlite3.connect('%s')
inserted = 0
for d in cells:
    if c.execute('select 1 from mem_cells where content_hash = ?', (d['content_hash'],)).fetchone():
        continue
    emb = base64.b64decode(d['embedding']) if d.get('embedding') else None
    cur = c.execute('''insert into mem_cells
       (scene, cell_type, salience, content, source, tags, embedding,
        access_count, created_at, updated_at, content_hash, owner_id, visibility)
       values (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
       (d['scene'], d['cell_type'], d['salience'], d['content'], d['source'],
        d['tags'], emb, d.get('access_count', 0), d['created_at'], d['updated_at'],
        d['content_hash'], d.get('owner_id', 'u0am4blbuuw'), d.get('visibility', 'private')))
    c.execute('insert into mem_fts(rowid, content, scene, cell_type, tags) values (?,?,?,?,?)',
        (cur.lastrowid, d['content'], d['scene'], d['cell_type'], d['tags']))
    inserted += 1
c.commit()
print(json.dumps({'inserted': inserted, 'cells': c.execute('select count(*) from mem_cells').fetchone()[0]}))
"""


def local_hashes(conn):
    return set(h for (h,) in conn.execute("select content_hash from mem_cells where content_hash is not null"))


def local_cells_for(conn, hashes):
    if not hashes:
        return []
    conn.row_factory = sqlite3.Row
    q = "select * from mem_cells where content_hash in ({})".format(",".join("?" * len(hashes)))
    rows = conn.execute(q, list(hashes)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("embedding") is not None:
            d["embedding"] = base64.b64encode(d["embedding"]).decode()
        out.append(d)
    return out


def apply_local(conn, cells):
    inserted = 0
    for d in cells:
        if conn.execute("select 1 from mem_cells where content_hash = ?", (d["content_hash"],)).fetchone():
            continue
        emb = base64.b64decode(d["embedding"]) if d.get("embedding") else None
        cur = conn.execute(
            """insert into mem_cells
               (scene, cell_type, salience, content, source, tags, embedding,
                access_count, created_at, updated_at, content_hash, owner_id, visibility)
               values (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d["scene"], d["cell_type"], d["salience"], d["content"], d["source"],
             d["tags"], emb, d.get("access_count", 0), d["created_at"], d["updated_at"],
             d["content_hash"], d.get("owner_id", "u0am4blbuuw"), d.get("visibility", "private")),
        )
        conn.execute(
            "insert into mem_fts(rowid, content, scene, cell_type, tags) values (?,?,?,?,?)",
            (cur.lastrowid, d["content"], d["scene"], d["cell_type"], d["tags"]),
        )
        inserted += 1
    conn.commit()
    return inserted


def main():
    try:
        pod = remote_pod_name()
    except Exception as e:
        log(f"[archy-reconcile] remote unreachable, skipping this cycle: {e}")
        return 0

    conn = sqlite3.connect(LOCAL_DB)
    try:
        mac_hashes = local_hashes(conn)

        remote_hashes_raw = remote_exec(pod, DUMP_HASHES_PY)
        swabby_hashes = set(remote_hashes_raw.decode().split())

        mac_only = mac_hashes - swabby_hashes
        swabby_only = swabby_hashes - mac_hashes

        if not mac_only and not swabby_only:
            log(f"[archy-reconcile] already in sync ({len(mac_hashes)} cells)")
            return 0

        pushed = 0
        if mac_only:
            payload = json.dumps(local_cells_for(conn, mac_only)).encode()
            remote_exec(pod, APPLY_CELLS_PY, input_data=payload)
            pushed = len(mac_only)

        pulled = 0
        if swabby_only:
            hash_list = "\n".join(swabby_only).encode()
            cells_raw = remote_exec(pod, DUMP_CELLS_PY, input_data=hash_list)
            cells = json.loads(cells_raw)
            pulled = apply_local(conn, cells)

        log(f"[archy-reconcile] pushed {pushed} mac-only cells to swabby, "
            f"pulled {pulled} swabby-only cells to mac")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
