# S2.2 Real Control Path — PASS

Date: 2026-09-09
Environment: staging VDS
Deployed release: `zest-canonical-phase8-rc3-browser-page-fanout-hotfix`
Recovered product baseline: `ae89df540893a972866ce0e7f91765bf7a0244f6`
External probe revision: `9caf421202641a91bb5c50b964df1f6950b14e7f`

## Result

`S2_2_RESULT=PASS`

A fresh controlled Gate22 run proved the deployed authoritative control path end-to-end:

`dashboard bootstrap -> zestd preflight -> zestd START -> supervised real target contact -> WorkerResult/Observation -> operator cancel -> terminal cleanup`

## Observed run

- Gate22 origin: `http://127.0.0.1:34013`
- ResearchRun: `a0346893-84f7-49dd-9a0b-1a419ea98238`
- Preflight: `READY_TO_START`
- START response state: `READY`
- First poll: cycle `0`, `CYCLE_READY`, locally supervised, no work yet
- Next observed poll: cycle `1`, `CYCLE_COMPLETE`, locally supervised, request count `64`, Worker count `1`, model count `0`, hypothesis count `1`, experiment count `1`, observation count `1`
- Independent target-side proof: seed `GET /` observed by Gate22 lab
- Cancel response: `COMPLETED / OPERATOR_CANCELLED`
- Final cancel verification: local supervisor detached

Checks:

- `bootstrap_startable=PASS`
- `preflight_ready=PASS`
- `start_returned_run=PASS`
- `target_received_seed=PASS`
- `worker_and_observation_seen=PASS`
- `cancel_verified=PASS`

The probe exited `0` and wrote `/tmp/zest-s2-control-result.json` on the VDS.

## Interpretation

This is a control-path qualification, not research-quality success. It proves that one fresh authoritative run can be created, reconstructed/preflighted by zestd, started under local supervision, contact the controlled target through the real Worker path, persist an Observation, and be cleanly stopped through the operator cancel path.

The observed `model_count=0` is expected for this S2 probe because it intentionally cancels immediately after the first real WorkerResult/Observation. It is not evidence that the full research/model path is absent.

## Gate decision

S2 — Real control path: **PASS**.

S3/S4 must use a new fresh ResearchRun. The cancelled S2 run is never reused as START-to-terminal success evidence.

No product behavior was modified to obtain this result.
