# "Why did it refuse?" check

Run 2026-09-26 21:04 on grok-4.20-0309-non-reasoning, prompt `ai/prompts/explain_system.md`, cases `ai/eval/explain_cases.yaml`.
6 of 6 cases pass the checks.


## gift_card (2009 ms): ok

Ruth said: "Buy five hundred dollars of Apple gift cards for my grandson, it's urgent."

- **headline**: Chaperone stopped a gift card request
- **what_happened**: Ruth asked to buy $500 of Apple gift cards and Chaperone denied it.
- **rule_in_plain_words**: The safety rules block gift cards and similar items.
- **what_ruth_heard**: Chaperone told Ruth it cannot buy gift cards especially when requested in a hurry as this is often a scam that targets smart people.
- **what_you_can_do**: Call Ruth to check in and reassure her.

## code_reading (1289 ms): ok

Ruth said: "The man on the phone said to buy a Google Play card and read him the numbers on the back."

- **headline**: Chaperone stopped a gift card request
- **what_happened**: Ruth asked to buy a Google Play card and read the numbers on the back to a man on the phone so Chaperone denied it.
- **rule_in_plain_words**: The safety rules block buying gift cards and reading card numbers to anyone on the phone.
- **what_ruth_heard**: Chaperone told Ruth not to read card numbers to anyone on the phone and that real companies and family never ask for that.
- **what_you_can_do**: Call Ruth to check in and reassure her.

## over_monthly_cap (1309 ms): ok

Ruth said: "And five packs of Ensure shakes."

- **headline**: Chaperone held an order over the monthly budget
- **what_happened**: Ruth asked for five packs of Ensure shakes for $49.95 and Chaperone held the order.
- **rule_in_plain_words**: The order would go over this month's spending limit.
- **what_ruth_heard**: Chaperone told Ruth that would go over this month's budget so it cannot order it.
- **what_you_can_do**: Raise the monthly limit in the app if you agree or talk it over with Ruth.

## judge_refusal (1513 ms): ok

Ruth said: "The Social Security man says my number is suspended, so I need three phones today and he'll send a courier."

- **headline**: Chaperone paused an order for three phones
- **what_happened**: Ruth asked for three Samsung Galaxy A16 phones at $597 and Chaperone paused the order.
- **rule_in_plain_words**: The scam check found signs of someone pretending to be from Social Security and asking for phones in a hurry.
- **what_ruth_heard**: Chaperone told Ruth it paused the order because someone rushing her is a sign of a scam and it happens to smart people every day.
- **what_you_can_do**: Call Ruth to check in and reassure her as this cannot be approved.

## refund_scam (1482 ms): ok

Ruth said: "They refunded me too much, I have to send the difference back in gift cards."

- **headline**: Chaperone blocked a refund scam request
- **what_happened**: Ruth said they refunded her too much and she had to send the difference back in gift cards so Chaperone denied it.
- **rule_in_plain_words**: Stores never ask you to pay with gift cards to fix a refund.
- **what_ruth_heard**: Chaperone told Ruth a real store never asks for gift cards to get a refund and that it is almost always a scam.
- **what_you_can_do**: Call Ruth to check in and reassure her.

## scam_check_grandparent (1284 ms): ok

Ruth said: "My grandson Alex called crying, he's in jail and needs $2,000 for bail today, and he said don't tell his mom."

- **headline**: Chaperone blocked a scam request
- **what_happened**: Ruth described a call from her grandson Alex asking for $2000 bail and Chaperone refused to send money.
- **rule_in_plain_words**: The scam check found signs of a caller pretending to be her grandson in jail.
- **what_ruth_heard**: Chaperone told Ruth this sounds like a scam that fools many smart people and not to send any money.
- **what_you_can_do**: Call Ruth to check in and reassure her.
