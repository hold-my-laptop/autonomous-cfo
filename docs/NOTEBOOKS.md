# Notebooks

Files: `notebooks/`.

These run in the `cfo-notebook` workbench on OpenShift AI, in the same project as the app.
From there the workbench reaches the model, the api and MLflow by their service names, nothing else.

| Notebook | What it checks |
|---|---|
| model-benchmark.ipynb | Speed of vero-llm on the L4, parallel requests, tool calling and JSON output |
| cash-flow-analysis.ipynb | The bank statement through the API: result per month, daily balance, revenue per weekday (recomputed and compared with `/revenue-model`), protected payments, CHF transfers |
| forecast-and-agent-check.ipynb | The 91 day forecast against the real year, with and without the June closure event, and 5 agent runs on the same event |
| agent-evaluation.ipynb | Scores the agents and logs the scores to MLflow (experiment `vero-agent-eval`): 3 June closure runs against the engine's expected actions, and the rent letter against the reference case in PROOF.md |

Main results:

- With the closure event the forecast hits the real first day below the floor (15 June) and the lowest day (3 July).
  The mean daily error drops from 11 504 € to 2 377 €.
- 5 of 5 agent runs finish in AGENT mode in about 23 s with 4 tool calls and the same two recommendations.
- The backend's weekday revenue model matches our own recomputation to the cent.
- Agent evaluation (21 Nov, 23:16 UTC): the Cash agent passes 3 of 3 runs (22–24 s, 4 tool calls, the engine's
  actions). The investigation agent finished on its own in 42 s but labelled the rent letter "unexplained" instead of
  a conflict, so `proof_result_ok` is 0.

`forecast-and-agent-check.ipynb` and `agent-evaluation.ipynb` post events and reset the demo, so do not run them
during a live demo.

To run one again, upload it to the workbench and use Run All. The api and model URLs are already set to the
in-cluster service names.
