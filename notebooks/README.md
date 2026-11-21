# Notebooks

These run in the `cfo-notebook` workbench on OpenShift AI, in the same project as the app.
From there the workbench reaches the model and the api by their service names, nothing else.

| Notebook | What it checks |
|---|---|
| model-benchmark.ipynb | Speed of vero-llm on the L4, parallel requests, tool calling and JSON output |
| cash-flow-analysis.ipynb | The bank statement through the API: result per month, daily balance, revenue per weekday (recomputed and compared with `/revenue-model`), protected payments, CHF transfers |
| forecast-and-agent-check.ipynb | The 91 day forecast against the real year, with and without the June closure event, and 5 agent runs on the same event |

Main results:

- With the closure event the forecast hits the real first day below the floor (15 June) and the lowest day (3 July).
  The mean daily error drops from 11 504 € to 2 377 €.
- 5 of 5 agent runs finish in AGENT mode in about 23 s with 4 tool calls and the same two recommendations.
- The backend's weekday revenue model matches our own recomputation to the cent.

`forecast-and-agent-check.ipynb` posts events and resets the demo, so do not run it during a live demo.

To run one again, upload it to the workbench and use Run All. The api and model URLs are already set to the
in-cluster service names.
