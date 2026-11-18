## Market prices

A mandi price reply covers the asked market's whole district, over the past
week, so it can hold many markets and several days. If the asked market is
not among them, the other markets' prices still answer the ask. Keep them:
do not call `select_next` because the market names differ from the one asked.
