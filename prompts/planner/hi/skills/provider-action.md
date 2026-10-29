---
# DEMO — हिंदी provider-action skill. A demonstration override showing the
# per-language prompt mechanism. NOT backed by an eval; PENDING review by a
# Hindi speaker. The frontmatter (id, domain, description, tool_names,
# input_kind) must stay IDENTICAL to the English file — tool_names wires the
# agent's tools, and a translated tool name would wire nothing.
id: provider-action
domain: agriculture
description: File a submitted form with a provider.
tool_names: [describe_capability, support]
input_kind: form
---
"files the farmer's form" चिह्नित ask का उत्तर `support` tool देता है, जो
फ़ॉर्म के मान भेजता है। पहले `describe_capability` बुलाएँ ताकि दिखे कौन से
provider फ़ॉर्म ले सकते हैं। एक चुनें, और उसकी resource_id के साथ `support`
एक बार बुलाएँ।

आप कोई field नहीं लिखते। फ़ॉर्म में हर मान पहले से है, और tool उसी से call
बनाता है; आपके भरने या जाँचने को कुछ नहीं है।

यदि `support` बताए कि फ़ॉर्म दर्ज नहीं हुआ, तो उस ask के लिए उसे दोबारा न
बुलाएँ। बताएँ कि दर्ज नहीं हुआ और रुक जाएँ। दर्ज होने की सूचना मिलते ही रुक
जाएँ।
