# One host on AWS

`compose.yml` layers over the root `docker-compose.yml` for a single internet-facing host:

- Web traffic arrives through a Cloudflare tunnel run on the host (`cloudflared` service), so no inbound 80 or 443 is needed. The tunnel sends `DAFTER_PUBLIC_HOST` to the control plane on `127.0.0.1:8080` (except `/metrics`, which answers 404) and `DAFTER_SFU_HOST` to LiveKit signaling on `127.0.0.1:7880`.
- Media is direct: LiveKit advertises `LIVEKIT_NODE_IP` and is the only service with public ports, 7881/tcp and 7882/udp. Its config is inline in `compose.yml` (`LIVEKIT_CONFIG`) so its webhook can name the API key; it posts to the control plane at `http://control:8080/livekit/webhook`.
- Phone audio: the SIP bridge finds its public address over STUN (it logs `externalIP` at debug level) and takes RTP on 10000-10100/udp, which the security group opens only to Vobiz's published India media ranges (https://vobiz.ai/docs/concepts/ip-whitelisting). SIP signaling is outbound only (the media server calls Vobiz for dial-out and to reach a dial-in caller), so 5060 stays closed.
- Recording needs S3 credentials: the control plane only receives `DAFTER_EGRESS_S3_BUCKET` when `DAFTER_EGRESS_S3_ACCESS_KEY` is set. `tenant-recorded.json` records every session from creation after a consent notice: one mixed MP4 of the call, plus each voice (every published audio track) in its own file, started by the control plane when LiveKit reports the track, and live captions from the agent. `tenant.json` records nothing.
- Everything else binds to localhost, except SIP (5060) and the egress health port (9095), which run on host networking and are closed by the security group. The agent worker starts with the stack; the scribe worker is behind the `scribe` profile.

## Environment

The host's `.env` (mode 600) holds every credential, plus:

```
COMPOSE_FILE=docker-compose.yml:deploy/aws/compose.yml
COMPOSE_PROJECT_NAME=dafter
LIVEKIT_NODE_IP=<elastic ip>
DAFTER_PUBLIC_HOST=<web hostname>
DAFTER_SFU_HOST=<signaling hostname>
DAFTER_LIVEKIT_PUBLIC_URL=wss://<signaling hostname>
DAFTER_EGRESS_S3_BUCKET=<bucket>
DAFTER_EGRESS_S3_REGION=<region>
DAFTER_CF_TUNNEL_ID=<tunnel uuid>
DAFTER_CF_TUNNEL_CREDENTIALS=/etc/dafter/tunnel.json
```

`DAFTER_CF_TUNNEL_TOKEN` (the dev tunnel) is never set here.

## Tunnel

From a machine logged in with `cloudflared tunnel login`:

```
cloudflared tunnel create --credentials-file tunnel.json <name>
cloudflared tunnel route dns --overwrite-dns <name> <web hostname>
cloudflared tunnel route dns --overwrite-dns <name> <signaling hostname>
```

Copy only `tunnel.json` to the host, readable by the cloudflared container user and nobody else:

```
sudo install -d -m 700 -o 65532 -g 65532 /etc/dafter
sudo install -m 600 -o 65532 -g 65532 tunnel.json /etc/dafter/tunnel.json
```

## Start

```
cd /opt/dafter
sudo docker compose up -d
set -a; . ./.env; set +a; ./deploy/aws/apply-tenant.sh
```

`apply-tenant.sh` publishes `tenant.json` (or the file given as its argument) and `channel-webrtc.json`, the web channel overlay that sets how the mixed recording is encoded (960x540 at 15 fps, so one egress host keeps up with a call, its agent and every voice). It is needed once; both survive restarts.

## Turn recording on

An administrator creates an IAM user whose only policy allows `s3:PutObject` (and `s3:AbortMultipartUpload`, which a large MP4 needs) on `arn:aws:s3:::<bucket>/*`, and hands over its access key. The resource must end in `/*`: `arn:aws:s3:::<bucket>/` matches no object, so every upload is refused with AccessDenied.

1. Add to the operator's env file, then copy it to the host's `.env` (mode 600):

   ```
   DAFTER_EGRESS_S3_ACCESS_KEY=<access key id>
   DAFTER_EGRESS_S3_SECRET=<secret access key>
   ```

2. On the host, restart the control plane and publish the recorded tenant:

   ```
   cd /opt/dafter
   sudo docker compose up -d control
   set -a; . ./.env; set +a; ./deploy/aws/apply-tenant.sh deploy/aws/tenant-recorded.json
   ```

The control plane logs `recording enabled` on start, and `voice recorded on its own` for each voice. Every new session then records from creation, joiners see the notice before joining and the badge during the call, and each call lands in the bucket once everyone leaves as `<room>/room_composite-<utc>.mp4` beside one `<room>/track-<track>-<utc>.ogg` per voice. `GET /sessions/<room>` lists the recordings with whose voice each track is. To turn it off again, publish `tenant.json` and remove the two lines.

## Recordings

With your own AWS credentials and `BUCKET` set:

```
aws s3 sync "s3://$BUCKET" ./recordings --region <region>
aws s3 presign "s3://$BUCKET/<room>/room_composite-<utc>.mp4" --expires-in 604800 --region <region>
```

The first downloads every recording; the second prints a link to one that works for up to 7 days. `ffprobe <file>` shows a recording's streams and length.
