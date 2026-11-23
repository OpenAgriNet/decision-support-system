## Market prices

Send `validity` on a mandi price `select`: the days to get prices for.
`startsAt` is 00:00:00 on the first day and `endsAt` is 23:59:59 on the last,
both in India time (`+05:30`). Work the days out from today's date:
- No day or period in the question: the 7 days before today, through today.
- One day ("yesterday", "on 3 November"): the 7 days before that day,
  through that day.
- A period ("from 1 to 10 November", "last week"): that period as asked.
- A day or period that ends after today: as if no day was named.

For example, if today is 2026-11-23, "onion price on 3 November" sends
`{"startsAt": "2026-10-27T00:00:00+05:30", "endsAt": "2026-11-03T23:59:59+05:30"}`.

A mandi price reply covers the asked market's whole district over those days,
so it can hold many markets and several days. If the asked market is not
among them, the other markets' prices still answer the ask. Keep them: do not
call `select_next` because the market names differ from the one asked.
