# Deploying oc-memory on Kubernetes (k3s)

This guide walks through building the oc-memory container image, loading it into k3s, deploying with the provided manifest, and connecting the `mem` CLI.

## Prerequisites

- k3s cluster running (single-node or multi-node)
- `kubectl` configured and pointing at your cluster
- Container build tool: Docker **or** nerdctl (for k3s/containerd clusters)
- `local-path` storage class available (k3s default)

---

## Step 1: Build the Image

### Using Docker

```bash
cd ~/repos/oc-memory
docker build -t oc-memory:latest .
```

### Using nerdctl (k3s/containerd, e.g. Raspberry Pi)

k3s uses containerd directly. Build with nerdctl into the `k8s.io` namespace so k3s can find it without a registry push:

```bash
# nerdctl must run as root to access the k3s containerd socket
sudo nerdctl build -t oc-memory:latest .
```

Verify the image is visible to k3s:

```bash
sudo k3s ctr images ls | grep oc-memory
```

---

## Step 2: Load the Image into k3s (Docker builds only)

If you built with Docker and your k3s node doesn't share the Docker daemon, export and import the image:

```bash
# Export from Docker
docker save oc-memory:latest | gzip > /tmp/oc-memory.tar.gz

# Import into k3s containerd (run on the k3s node)
sudo k3s ctr images import /tmp/oc-memory.tar.gz
```

If you built with nerdctl into the `k8s.io` namespace, skip this step — the image is already visible to k3s.

---

## Step 3: Configure the Manifest

The manifest at [`k8s/memory-server.yaml`](../k8s/memory-server.yaml) deploys oc-memory with:

- **Namespace:** `oc-memory`
- **Storage:** 5Gi PVC via `local-path` storage class
- **Service:** NodePort 30765 → container port 8765
- **Image pull policy:** `Never` (uses locally loaded image)
- **Node selector:** `kubernetes.io/hostname: worker-pi` (update to your node name)

**Before applying, edit these values:**

```yaml
# Set to the hostname of the node where you want the pod to run
nodeSelector:
  kubernetes.io/hostname: worker-pi   # ← change this

# If using a different storage class:
storageClassName: local-path   # ← change if needed
```

To find your node names:

```bash
kubectl get nodes
```

---

## Step 4: Deploy

```bash
kubectl apply -f k8s/memory-server.yaml
```

---

## Step 5: Verify the Deployment

Check that the pod is running:

```bash
kubectl get pods -n oc-memory
```

Expected output:
```
NAME                             READY   STATUS    RESTARTS   AGE
memory-server-xxxxxxxxx-xxxxx    1/1     Running   0          30s
```

Check the service:

```bash
kubectl get svc -n oc-memory
```

Expected output:
```
NAME             TYPE       CLUSTER-IP     EXTERNAL-IP   PORT(S)          AGE
memory-server    NodePort   10.43.x.x      <none>        8765:30765/TCP   30s
```

Check the health endpoint:

```bash
curl http://<node-ip>:30765/health
```

Should return `{"status": "ok"}` or similar.

View logs:

```bash
kubectl logs -n oc-memory -l app=memory-server --tail=20
```

---

## Step 6: Connect the `mem` CLI

Set `MEM_MCP_URL` to the NodePort endpoint:

```bash
export MEM_MCP_URL="http://<node-ip>:30765"

# Add to ~/.bashrc to persist:
echo 'export MEM_MCP_URL="http://<node-ip>:30765"' >> ~/.bashrc
source ~/.bashrc
```

Test the connection:

```bash
mem stats
```

---

## Updating the Deployment

To rebuild and redeploy after code changes:

```bash
# Rebuild (nerdctl example)
sudo nerdctl build -t oc-memory:latest .

# Force a rolling restart to pick up the new image
kubectl rollout restart deployment/memory-server -n oc-memory

# Watch the rollout
kubectl rollout status deployment/memory-server -n oc-memory
```

---

## Litestream Replication (for local-replica clients)

The bundled image (see the parent `swabby-memory` repo's `Dockerfile` +
`docker/entrypoint.sh`) runs `litestream replicate` alongside the MCP server,
shipping WAL changes to a local file replica at `/data/litestream-replica`.
The manifest mounts a dedicated `litestream-replica` volume (separate from
the primary `/data` PVC) at that path.

This is what a remote client uses to keep a fast local read copy via
`litestream restore -f` — see `oc_memory/docs/local-replica.md` for the
client-side setup. No action is needed here beyond deploying the updated
manifest; the replica stream runs unconditionally and costs a small amount
of CPU/memory (accounted for in the container's resource limits).

---

## Persistent Data

The database is stored on the PVC at `/data/memory.db`. To back it up:

```bash
# Get the pod name
POD=$(kubectl get pods -n oc-memory -l app=memory-server -o jsonpath='{.items[0].metadata.name}')

# Copy the database locally
kubectl cp oc-memory/$POD:/data/memory.db ./memory-backup.db
```

---

## Troubleshooting

**Pod stuck in `Pending`:**
- Check node selector matches your actual node name: `kubectl get nodes`
- Check PVC is bound: `kubectl get pvc -n oc-memory`

**Pod `CrashLoopBackOff`:**
- View logs: `kubectl logs -n oc-memory -l app=memory-server`
- Common cause: image not found. Verify: `sudo k3s ctr images ls | grep oc-memory`

**`mem stats` times out:**
- Verify NodePort is reachable: `curl http://<node-ip>:30765/health`
- Check firewall rules on the node

**Image pull errors with `imagePullPolicy: Never`:**
- Make sure the image is loaded in the `k8s.io` containerd namespace, not `default`
- Use `sudo k3s ctr images ls` (not `docker images`) to verify
