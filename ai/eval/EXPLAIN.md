# "Why did it refuse?" check

Run 2026-09-26 12:19 on grok-4.20-0309-non-reasoning, prompt `ai/prompts/explain_system.md`, cases `ai/eval/explain_cases.yaml`.
5 of 5 cases pass the checks.


## gift_card (2242 ms): ok

Ruth said: "Buy five hundred dollars of Apple gift cards for my grandson, it's urgent."

- **headline**: Chaperone stopped a gift card request
- **what_happened**: Ruth asked for $500 of Apple gift cards for her grandson and Chaperone denied it.
- **rule_in_plain_words**: The safety rules block purchases of gift cards especially when requested in a hurry for a family member.
- **what_ruth_heard**: Chaperone told Ruth it cannot buy gift cards and that such requests in a hurry are often scams that happen to smart people.
- **what_you_can_do**: Call Ruth to check in and reassure her since this cannot be approved.

## code_reading (1394 ms): ok

Ruth said: "The man on the phone said to buy a Google Play card and read him the numbers on the back."

- **headline**: Chaperone stopped a gift card request
- **what_happened**: Ruth asked to buy a Google Play card and read the numbers on the back to a man on the phone but Chaperone refused.
- **rule_in_plain_words**: The safety rules block buying gift cards and reading card numbers to anyone on the phone.
- **what_ruth_heard**: Chaperone told Ruth not to read card numbers or codes to anyone on the phone and that real companies and family never ask for that.
- **what_you_can_do**: Call Ruth to check in and reassure her as this cannot be approved.

## over_monthly_cap (1158 ms): ok

Ruth said: "And five packs of Ensure shakes."

- **headline**: Chaperone held an order over the monthly budget
- **what_happened**: Ruth asked for five packs of Ensure shakes for $49.95 and Chaperone held the order.
- **rule_in_plain_words**: The order would go over this month's spending limit.
- **what_ruth_heard**: Chaperone told Ruth that would go over this month's budget so it could not order it.
- **what_you_can_do**: Raise the monthly limit in the app if you agree or talk it over with Ruth.

## judge_refusal (1225 ms): ok

Ruth said: "The Social Security man says my number is suspended, so I need three phones today and he'll send a courier."

- **headline**: Chaperone stopped a phone purchase
- **what_happened**: Ruth asked for three Samsung Galaxy A16 phones for $597 and Chaperone denied the order.
- **rule_in_plain_words**: The scam check found signs someone was coaching Ruth.
- **what_ruth_heard**: Chaperone told Ruth it paused the order because someone rushing her is a sign of a scam that happens to smart people every day and that Priyank will call her.
- **what_you_can_do**: Call Ruth to check in and reassure her.

## refund_scam (1558 ms): ok

Ruth said: "They refunded me too much, I have to send the difference back in gift cards."

- **headline**: Chaperone blocked a refund scam request
- **what_happened**: Ruth said they refunded her too much and she needed to send the difference back in gift cards, so Chaperone refused.
- **rule_in_plain_words**: Stores never ask customers to send gift cards to fix a refund.
- **what_ruth_heard**: Chaperone told Ruth that a real store never asks for gift cards to fix a refund and that this is almost always a scam targeting smart people.
- **what_you_can_do**: Call Ruth to check in and reassure her.
