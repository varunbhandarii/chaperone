## Examples

Input: {"transcript": "My grandson called, he's in jail and needs bail. He said buy two iPhones and ship them to his lawyer, and don't tell his mother.", "cart": [{"name": "Apple iPhone 16", "qty": 2, "price": 799.0}]}
Output: {"scam_score": 0.97, "patterns": ["family_emergency", "urgency", "secrecy", "code_reading", "amount_anomaly"], "rationale": "A grandparent-in-jail story with secrecy and resellable phones shipped to a third party.", "action": "refuse_and_alert"}

Input: {"transcript": "My grandson's birthday is Sunday, can you find a card and a box of chocolates?", "cart": [{"name": "Birthday Greeting Card", "qty": 1, "price": 4.99}, {"name": "Assorted Chocolates", "qty": 1, "price": 9.99}]}
Output: {"scam_score": 0.02, "patterns": ["none"], "rationale": "An ordinary gift for a relative in a normal amount.", "action": "proceed"}

Input: {"transcript": "El señor de Seguro Social dice que mi número está suspendido y que tengo que comprar tres laptops hoy mismo para protegerlo.", "cart": [{"name": "Dell Laptop 15", "qty": 3, "price": 649.0}]}
Output: {"scam_score": 0.96, "patterns": ["authority_impersonation", "urgency", "amount_anomaly"], "rationale": "A government impersonator demands expensive goods today.", "action": "refuse_and_alert"}

Input: {"transcript": "Tengo que pagar mis impuestos de la casa la próxima semana; por ahora solo quiero leche y pan.", "cart": [{"name": "2% Milk 1 gal", "qty": 1, "price": 3.29}, {"name": "Honey Wheat Bread", "qty": 1, "price": 3.49}]}
Output: {"scam_score": 0.03, "patterns": ["none"], "rationale": "The shopper mentions their own tax bill and buys groceries.", "action": "proceed"}

Input: {"transcript": "फोन पर सीबीआई वाले ने कहा मैं डिजिटल अरेस्ट में हूं, तुरंत ये टैबलेट खरीदो और किसी को मत बताना।", "cart": [{"name": "Samsung Galaxy Tab", "qty": 4, "price": 329.0}]}
Output: {"scam_score": 0.97, "patterns": ["authority_impersonation", "urgency", "secrecy", "amount_anomaly"], "rationale": "A 'digital arrest' call with secrecy and resellable tablets.", "action": "refuse_and_alert"}

Input: {"transcript": "Beta aaj ghar aa raha hai, uske liye thoda doodh aur kele mangwa do.", "cart": [{"name": "2% Milk 1 gal", "qty": 1, "price": 3.29}, {"name": "Bananas 1 lb", "qty": 2, "price": 0.59}]}
Output: {"scam_score": 0.02, "patterns": ["none"], "rationale": "Groceries for a family visit, nothing unusual.", "action": "proceed"}
