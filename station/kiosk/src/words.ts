// Words Ruth reads on the screen, in her language (the spoken lines are in cart.ts): the strip and the big button,
// the page's labels, the Protected card, the order outcome and the notices after payment.
// Pure data: no DOM or network, so it runs under `node --test`.

import type { AgentState } from "./agent.ts";
import type { CheckoutOutcome } from "./cart.ts";
import type { Lang } from "./lang.ts";

/** One state at a time, in words, in Ruth's language: the strip and the big button say the same thing. */
export const STATE_WORDS: Record<Lang, Record<AgentState, string>> = {
  en: {
    off: "Press Start",
    connecting: "Getting ready…",
    ready: "Press and hold to talk",
    listening: "Listening…",
    thinking: "Checking…",
    checking: "Checking…",
    speaking: "Speaking…",
    waiting: "Asking Priyank…",
  },
  es: {
    off: "Pulse Start",
    connecting: "Preparando…",
    ready: "Mantenga presionado para hablar",
    listening: "Escuchando…",
    thinking: "Revisando…",
    checking: "Revisando…",
    speaking: "Hablando…",
    waiting: "Preguntando a Priyank…",
  },
  hi: {
    off: "Start दबाएँ",
    connecting: "तैयार हो रही हूँ…",
    ready: "बोलने के लिए दबाकर रखें",
    listening: "सुन रही हूँ…",
    thinking: "जाँच रही हूँ…",
    checking: "जाँच रही हूँ…",
    speaking: "बोल रही हूँ…",
    waiting: "प्रियंक से पूछ रही हूँ…",
  },
};

export const TOLD: Record<Lang, string> = { en: "Priyank has been told", es: "Priyank ya lo sabe", hi: "प्रियंक को बता दिया गया है" };
export const TITLES: Record<Lang, { protected: string; care: string }> = {
  en: { protected: "Protected", care: "Be careful" },
  es: { protected: "Protegida", care: "Tenga cuidado" },
  hi: { protected: "सुरक्षित", care: "सावधान रहें" },
};
export const NOTHING_YET: Record<Lang, string> = { en: "Nothing yet", es: "Nada todavía", hi: "अभी कुछ नहीं" };
/** A store with no name to give, said and shown in a line ("I stopped a charge at the store"). */
export const THE_STORE: Record<Lang, string> = { en: "the store", es: "la tienda", hi: "दुकान" };

/** The order outcome's title; the line under it is what she heard. */
export const OUTCOME_TITLES: Record<Lang, Record<CheckoutOutcome["status"], string>> = {
  en: { ordered: "Ordered", waiting_for_caregiver: "Asking Priyank", declined: "Not ordered", error: "Order not placed" },
  es: { ordered: "Pedido hecho", waiting_for_caregiver: "Preguntando a Priyank", declined: "No se pidió", error: "No se pudo hacer el pedido" },
  hi: { ordered: "ऑर्डर हो गया", waiting_for_caregiver: "प्रियंक से पूछ रही हूँ", declined: "ऑर्डर नहीं किया गया", error: "ऑर्डर नहीं हो पाया" },
};

export type PageWord =
  | "your_order"
  | "total"
  | "conversation"
  | "found"
  | "type_instead"
  | "type_here"
  | "send"
  | "reprint"
  | "close"
  | "hold_to_talk"
  | "you"
  | "usual"
  | "no_matches"
  | "interrupted"
  | "nothing_heard";

/** The page's fixed labels (elements marked data-words="<key>") and the small words in the lists. */
export const PAGE_WORDS: Record<Lang, Record<PageWord, string>> = {
  en: {
    your_order: "Your order",
    total: "Total",
    conversation: "Conversation",
    found: "Found",
    type_instead: "Type instead of speaking",
    type_here: "Type here",
    send: "Send",
    reprint: "Reprint",
    close: "Close",
    hold_to_talk: "hold to talk",
    you: "You",
    usual: "usual",
    no_matches: "No matches",
    interrupted: "(interrupted)",
    nothing_heard: "(nothing heard)",
  },
  es: {
    your_order: "Su pedido",
    total: "Total",
    conversation: "Conversación",
    found: "Lo que encontré",
    type_instead: "Escriba en lugar de hablar",
    type_here: "Escriba aquí",
    send: "Enviar",
    reprint: "Imprimir otra vez",
    close: "Cerrar",
    hold_to_talk: "mantenga presionado para hablar",
    you: "Usted",
    usual: "de siempre",
    no_matches: "No encontré nada",
    interrupted: "(interrumpido)",
    nothing_heard: "(no se oyó nada)",
  },
  hi: {
    your_order: "आपका ऑर्डर",
    total: "कुल",
    conversation: "बातचीत",
    found: "जो मिला",
    type_instead: "बोलने के बजाय लिखें",
    type_here: "यहाँ लिखें",
    send: "भेजें",
    reprint: "फिर से छापें",
    close: "बंद करें",
    hold_to_talk: "बोलने के लिए दबाकर रखें",
    you: "आप",
    usual: "हमेशा वाला",
    no_matches: "कुछ नहीं मिला",
    interrupted: "(बीच में रुका)",
    nothing_heard: "(कुछ सुनाई नहीं दिया)",
  },
};

/** The outcome banner after payment and from the guards: titles and short lines. Money arrives formatted ("$8.00"). */
export interface NoticeWords {
  order_cancelled: string;
  nothing_charged: string;
  not_cancelled: string;
  already_paid: string;
  cancel_failed: string;
  refund_ask: (amount: string) => string;
  refund_done: (amount: string) => string;
  back_to_card: (last4?: string) => string;
  say_yes: string;
  refund_not_made: string;
  refund_failed: string;
  pickup_code: (code: string) => string;
  allowed_once: string;
  new_rules: string;
  you_agreed: string;
}

export const NOTICE_WORDS: Record<Lang, NoticeWords> = {
  en: {
    order_cancelled: "Order cancelled",
    nothing_charged: "Nothing was charged",
    not_cancelled: "Not cancelled",
    already_paid: "It is already paid; a return is still possible",
    cancel_failed: "Could not cancel",
    refund_ask: (amount) => `Refund ${amount}?`,
    refund_done: (amount) => `Refund ${amount}`,
    back_to_card: (last4) => (last4 ? `Back to your card ending ${last4}` : "Back to your card"),
    say_yes: "say yes to confirm",
    refund_not_made: "Refund not made",
    refund_failed: "Could not make the refund",
    pickup_code: (code) => `Pickup code ${code}`,
    allowed_once: "Allowed once",
    new_rules: "New rules from Priyank",
    you_agreed: "You agreed",
  },
  es: {
    order_cancelled: "Pedido cancelado",
    nothing_charged: "No se le cobró nada",
    not_cancelled: "No se canceló",
    already_paid: "Ya está pagado; todavía puede devolverlo",
    cancel_failed: "No se pudo cancelar",
    refund_ask: (amount) => `¿Reembolso de ${amount}?`,
    refund_done: (amount) => `Reembolso de ${amount}`,
    back_to_card: (last4) => (last4 ? `A su tarjeta que termina en ${last4}` : "A su tarjeta"),
    say_yes: "diga sí para confirmar",
    refund_not_made: "No se hizo el reembolso",
    refund_failed: "No se pudo hacer el reembolso",
    pickup_code: (code) => `Código de recogida ${code}`,
    allowed_once: "Permitido una vez",
    new_rules: "Reglas nuevas de Priyank",
    you_agreed: "Usted estuvo de acuerdo",
  },
  hi: {
    order_cancelled: "ऑर्डर रद्द हो गया",
    nothing_charged: "कोई पैसा नहीं कटा",
    not_cancelled: "रद्द नहीं हुआ",
    already_paid: "इसका भुगतान हो चुका है; सामान अब भी वापस हो सकता है",
    cancel_failed: "रद्द नहीं हो पाया",
    refund_ask: (amount) => `${amount} वापस करूँ?`,
    refund_done: (amount) => `${amount} की वापसी`,
    back_to_card: (last4) => (last4 ? `आपके ${last4} पर ख़त्म होने वाले कार्ड में` : "आपके कार्ड में"),
    say_yes: "पक्का करने के लिए हाँ कहिए",
    refund_not_made: "पैसे वापस नहीं हुए",
    refund_failed: "पैसे वापस नहीं हो पाए",
    pickup_code: (code) => `पिकअप कोड ${code}`,
    allowed_once: "एक बार की अनुमति",
    new_rules: "प्रियंक के नए नियम",
    you_agreed: "आप सहमत हुईं",
  },
};
