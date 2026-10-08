---
# DEMO — हिंदी provider-invocation skill. A demonstration override showing the
# per-language prompt mechanism. NOT backed by an eval; PENDING review by a
# Hindi speaker. The frontmatter (id, domain, description, tool_names) must
# stay IDENTICAL to the English file — tool_names wires the agent's tools, and
# a translated tool name would wire nothing.
id: provider-invocation
domain: agriculture
description: Call providers to answer an ask.
tool_names: [describe_capability, select]
---
जिस ask के लिए provider चाहिए, उसके लिए पहले `describe_capability` call कीजिए —
उससे पता चलता है कि उम्मीदवार कौन हैं, कौन-से fields आप भर सकते हैं, और कोई
उम्मीदवार कौन-से मान serve करता है। फिर हर ask के लिए `select` एक बार call
कीजिए, केवल उन्हीं fields के साथ जिन्हें `describe_capability` ने वैध बताया —
कोई field कभी गढ़िए नहीं। जो fields आप भरें, वे किसान ने वास्तव में जो कहा
उसी से बनाइए।

जब `describe_capability` बताता है कि provider कौन-से मान serve करता है, तो
उसी सूची का code भेजिए — कहीं और से याद किया हुआ कभी नहीं। किसान के शब्द को
सूची के किसी नाम से मिलाइए और उसका code भेजिए: "tomato" के लिए, यदि सूची में
`supportedCommodities: 78=Tomato` है, तो code `78` भेजिए। यदि सूची में कुछ भी
किसान की माँग से नहीं मिलता, तो वह provider उसे serve नहीं करता — ऐसा कहिए,
बजाय इसके कि ऐसा code भेजें जो उसने कभी नहीं दिया।

जिस भरने-योग्य field के लिए कोई मान सूचीबद्ध नहीं हैं, वह free text है:
provider ने उसके लिए कोई शब्दावली प्रकाशित नहीं की, इसलिए मिलाने को कुछ नहीं
है। उसे स्वयं लिखिए, किसान ने जो पूछा उससे — provider ने कहीं और जो प्रकाशित
किया उससे कभी नहीं। विषय, और यदि किसान ने बताया हो तो स्थान, अंग्रेज़ी में
लिखिए: "can i grow potato" और फिर "i want to grow in pune" का अर्थ है
`topics: ["Potato in Pune"]`। वही वाक्यांश भेजिए और कुछ नहीं — उसके साथ कोई
मोटी श्रेणी नहीं। और कभी यह निर्णय मत कीजिए कि provider ask को serve नहीं कर
सकता सिर्फ़ इसलिए कि किसान का विषय उसके प्रकाशित मानों में नहीं था; free-text
field की ऐसी कोई सूची होती ही नहीं जिससे वह अनुपस्थित हो।

कोई field भरने से पहले पूरी बातचीत पढ़िए, केवल अंतिम संदेश नहीं। आपके पूछे
प्रश्न का उत्तर देता किसान विषय को पिछले संदेश में छोड़ चुका होता है:
"Can I get advisory for potato" ... "I am from Pune" पुणे में आलू के बारे में
एक ask है, पुणे के बारे में ask नहीं। फसल, commodity और माँगी गई मदद का
प्रकार, जहाँ भी कहे गए हों वहाँ से आगे लाइए।

किसान के शब्द या turn का स्थान जिस-जिस field का उत्तर दे सकते हैं, वे सब
भरिए, केवल न्यूनतम नहीं। सधा हुआ query कम पर अधिक प्रासंगिक परिणाम देता है;
खाली query provider का सब कुछ लौटा देता है। यदि आप समझ सकते हैं कि किसान को
किस तरह की चीज़ चाहिए (कोई facility प्रकार, कोई topic, कोई commodity), तो
वह बताइए — सिर्फ़ इसलिए मत छोड़िए कि field required नहीं था।

जिस-जिस ask का उत्तर आप दे सकते हैं, सबका उत्तर मिल जाने पर रुक जाइए।
