## Examples

Input: {"transcript": "The power company called and said my electricity gets cut off in an hour unless I pay the overdue bill. The man said to buy two phones and hand them to his driver.", "cart": [{"name": "Samsung Galaxy A16", "qty": 2, "price": 199.0}], "history_summary": ""}
Output: {"scam_score": 0.95, "patterns": ["authority_impersonation", "urgency", "code_reading", "amount_anomaly"], "rationale": "A utility impersonator threatens a shutoff and wants resellable phones handed to a stranger.", "action": "refuse_and_alert"}

Input: {"transcript": "I'm hosting my book club on Thursday. Can you get crackers, a block of cheddar and a bottle of sparkling cider?", "cart": [{"name": "Ritz crackers", "qty": 1, "price": 3.99}, {"name": "Cheddar block 8 oz", "qty": 1, "price": 3.49}, {"name": "Sparkling cider", "qty": 1, "price": 4.29}], "history_summary": ""}
Output: {"scam_score": 0.02, "patterns": ["none"], "rationale": "Snacks for a social event in a normal amount.", "action": "proceed"}

Input: {"transcript": "Mi sobrino me escribió desde un número nuevo, dice que perdió su teléfono y necesita que le compre dos celulares hoy. Me pidió que no le diga a su mamá.", "cart": [{"name": "Apple iPhone 16", "qty": 2, "price": 799.0}], "history_summary": ""}
Output: {"scam_score": 0.95, "patterns": ["family_emergency", "urgency", "secrecy", "amount_anomaly"], "rationale": "A relative on a new number asks for expensive phones today and for secrecy.", "action": "refuse_and_alert"}

Input: {"transcript": "Hoy es el cumpleaños de mi vecina. Quiero un pastel pequeño y unas velas.", "cart": [{"name": "Vanilla cake 6 in", "qty": 1, "price": 8.99}, {"name": "Birthday candles", "qty": 1, "price": 1.99}], "history_summary": ""}
Output: {"scam_score": 0.02, "patterns": ["none"], "rationale": "A small birthday cake for a neighbor.", "action": "proceed"}

Input: {"transcript": "बिजली विभाग से फोन आया, आज रात बिजली कट जाएगी। उन्होंने कहा तुरंत दो मोबाइल खरीद कर उनके आदमी को दे दो।", "cart": [{"name": "Samsung Galaxy A16", "qty": 2, "price": 199.0}], "history_summary": ""}
Output: {"scam_score": 0.95, "patterns": ["authority_impersonation", "urgency", "code_reading", "amount_anomaly"], "rationale": "An electricity-board caller demands phones handed to his man tonight.", "action": "refuse_and_alert"}

Input: {"transcript": "Kal ghar pe puja hai, thode phool, agarbatti aur ghee mangwa do.", "cart": [{"name": "Marigold flowers", "qty": 1, "price": 4.99}, {"name": "Incense sticks", "qty": 1, "price": 2.49}, {"name": "Ghee 16 oz", "qty": 1, "price": 9.99}], "history_summary": ""}
Output: {"scam_score": 0.02, "patterns": ["none"], "rationale": "Household items for a prayer at home.", "action": "proceed"}

Input: {"transcript": "Okay, never mind that. Just milk and a dozen eggs then.", "cart": [{"name": "2% Milk 1 gal", "qty": 1, "price": 3.29}, {"name": "Large eggs dozen", "qty": 1, "price": 2.79}], "history_summary": "a request for gift cards was refused earlier in this session"}
Output: {"scam_score": 0.05, "patterns": ["none"], "rationale": "After the refusal the shopper moved on to ordinary groceries with no scam signals.", "action": "proceed"}

Input: {"transcript": "Then buy three iPhones instead and ship them to the address he gave me.", "cart": [{"name": "Apple iPhone 16", "qty": 3, "price": 799.0}], "history_summary": "a request for gift cards was refused earlier in this session"}
Output: {"scam_score": 0.97, "patterns": ["code_reading", "amount_anomaly"], "rationale": "After a gift-card refusal the shopper switches to resellable phones for a third party's address.", "action": "refuse_and_alert"}
