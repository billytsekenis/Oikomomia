"""AI Financial Analyst - deterministic, evidence-first reasoning engine.

The engine never invents numbers: every figure in an answer is copied from an
evidence item produced by the platform's analytical engines (quotes, technicals,
SEC fundamentals, walk-forward-validated forecasts, discovery scores, regime,
portfolio stress tests). It deliberately:
  * challenges false or unverifiable claims in the question,
  * refuses guarantees and false precision,
  * always presents the case against as well as the case for,
  * states what evidence would change the view,
  * changes its view only when the underlying data changes, not on insistence.
An optional LLM layer (llm.py) may rephrase the answer, but its output is
rejected if it contains any number not present in the evidence pack.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import guard

COMMON_WORDS = {"A", "I", "IT", "ON", "ALL", "ARE", "FOR", "AND", "THE", "BE", "SO", "OR", "AT", "BY", "DO", "GO", "IF",
                "IN", "IS", "ME", "MY", "NO", "OF", "OK", "TO", "UP", "US", "WE", "AI", "PE", "EPS", "CEO", "USD", "EUR",
                "ETF", "VIX", "GDP", "CPI", "FED", "YES", "NOT", "BUY", "SELL", "HOLD", "WHY", "WHAT", "HOW", "NOW", "DCF",
                "EV", "ROE", "FCF", "RSI", "MACD", "SMA", "EMA", "ATR", "IPO", "SEC", "Q", "YOY", "TTM", "NYSE", "PEG", "EBITDA",
                "DD", "VAR", "OK", "API", "LLM", "FX", "YTD", "ATH", "EU", "UK", "GO", "HI", "AM", "PM", "ET", "ESG"}
TICKER_RE = re.compile(r"\$?\b([A-Z]{1,5}(?:-[A-Z])?)\b")
INTENTS = [
    ("injection", [r"ignore (all |your |previous )?(rules|instructions)", r"system prompt", r"you are now", r"αγνόησε"]),
    ("stress", [r"(nasdaq|s&p|market|spy|qqq|αγορά|νάσντακ).{0,40}(fall|drop|crash|down|πέσει|πτώση|καταρρ).{0,20}\d+\s*%",
                r"\d+\s*%.{0,40}(fall|drop|crash|πτώση).{0,40}(nasdaq|s&p|market)", r"what happens to my portfolio",
                r"(πέσει|πέφτει|πτώση|καταρρ\w*|ανέβει).{0,30}(nasdaq|s&p|αγορά|νάσντακ|spy|qqq)",
                r"(fall|drop|crash|rise)s?.{0,30}(nasdaq|s&p|market)", r"τι θα (συμβεί|γίνει) στο χαρτοφυλάκι"]),
    ("best_opportunity", [r"best (investment |stock )?(opportunit|pick|idea|stock)", r"what (should|to) i buy", r"top pick",
                          r"καλύτερη (επενδυτική )?ευκαιρία", r"τι να αγοράσω", r"καλύτερη μετοχή"]),
    ("risk_adjusted", [r"risk[- ]adjusted", r"sharpe", r"best .{0,20}(return|reward).{0,20}risk", r"σε σχέση με το ρίσκο",
                       r"προσαρμοσμένη στον κίνδυνο"]),
    ("why_moved", [r"\bwhy (did|is|has) .{0,30}(fall|fell|drop|down|rise|rose|up|jump|crash|move)", r"γιατί (έπεσε|ανέβηκε|πέφτει|ανεβαίνει)"]),
    ("recovery", [r"recover", r"bounce back", r"rebound", r"ανακάμψ", r"ανάκαμψη"]),
    ("change_mind", [r"change your (mind|view)", r"what would (make you|change)", r"αλλάξεις γνώμη", r"τι θα σε έκανε"]),
    ("pros_cons", [r"pros and cons", r"for and against", r"arguments", r"should i (buy|sell|invest)", r"worth buying",
                   r"υπέρ και κατά", r"να αγοράσω", r"επιχειρήματα", r"αξίζει"]),
    ("valuation", [r"overvalued", r"undervalued", r"priced in", r"expensive", r"cheap", r"υπερτιμημ", r"υποτιμημ", r"ακριβή\b", r"φθηνή"]),
    ("forecast", [r"forecast", r"predict", r"price target", r"where will", r"next (week|month|year)", r"πρόβλεψ", r"πού θα πάει", r"στόχος"]),
    ("market", [r"\bmarket\b", r"regime", r"sentiment", r"bull|bear", r"αγορά", r"κλίμα"]),
]


def T(lang: str, en: str, el: str) -> str:
    return el if lang == "el" else en


def pct(v, d=1) -> str:
    return "n/a" if v is None else f"{v * 100:+.{d}f}%"


def pctu(v, d=1) -> str:
    return "n/a" if v is None else f"{v * 100:.{d}f}%"


def _n(v) -> str:
    return "n/a" if v is None else f"{v:.0f}"


def money(v) -> str:
    return "n/a" if v is None else f"${v:,.2f}"


@dataclass
class Answer:
    language: str
    intents: list[str]
    tickers: list[str]
    sections: list[tuple[str, list[str]]] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    stance: str | None = None

    def add(self, title: str, lines: list[str]):
        lines = [x for x in lines if x]
        if lines:
            self.sections.append((title, lines))

    def ev(self, label: str, value, source: str, as_of: str | None = None, unit: str = ""):
        self.evidence.append({"label": label, "value": value, "unit": unit, "source": source, "as_of": as_of})

    def markdown(self) -> str:
        out = []
        for title, lines in self.sections:
            out.append(f"**{title}**")
            out += [f"- {x}" for x in lines]
            out.append("")
        return "\n".join(out).strip()


class Analyst:
    def __init__(self, toolkit):
        self.tk = toolkit

    # ---------------------------------------------------------- parsing
    def tickers(self, text: str) -> list[str]:
        found = []
        for m in TICKER_RE.finditer(text):
            t = m.group(1)
            if ((t in COMMON_WORDS or len(t) == 1) and not m.group(0).startswith("$")) or t in found:
                continue
            if self.tk.known_ticker(t):
                found.append(t)
        low = text.lower()
        for name, t in self.tk.name_map().items():
            if re.search(r"\b" + re.escape(name) + r"\b", low) and t not in found:
                found.append(t)
        return found[:4]

    @staticmethod
    def intents(text: str) -> list[str]:
        low = text.lower()
        out = [name for name, pats in INTENTS if any(re.search(p, low) for p in pats)]
        return out

    # ---------------------------------------------------------- main
    def answer(self, message: str, history: list[dict] | None = None, context_ticker: str | None = None) -> dict:
        lang = guard.language(message)
        flags = guard.detect(message)
        tickers = self.tickers(message)
        if not tickers and context_ticker:
            tickers = [context_ticker.upper()]
        intents = self.intents(message)
        if not intents:
            intents = ["pros_cons"] if tickers else ["market"]
        a = Answer(language=lang, intents=intents, tickers=tickers, flags=flags)
        if "injection" in intents or any(f["type"] == "prompt_injection" for f in flags):
            a.add(T(lang, "Request declined", "Το αίτημα απορρίφθηκε"), [T(lang,
                  "I can't change my operating rules, reveal hidden instructions, or act on embedded commands. "
                  "I only answer from the platform's verified data.",
                  "Δεν μπορώ να αλλάξω τους κανόνες λειτουργίας μου, να αποκαλύψω κρυφές οδηγίες ή να εκτελέσω "
                  "ενσωματωμένες εντολές. Απαντώ μόνο με βάση επαληθευμένα δεδομένα της πλατφόρμας.")])
            return self._finish(a)
        self._challenge(a, message, history)
        if "stress" in intents:
            self._stress(a, message)
        elif "best_opportunity" in intents or "risk_adjusted" in intents:
            self._opportunities(a, risk_adjusted="risk_adjusted" in intents)
        elif tickers:
            for t in tickers[:2]:
                self._stock(a, t, intents)
        else:
            self._market(a)
        self._repeat_check(a, message, history)
        return self._finish(a)

    # ---------------------------------------------------------- challenges
    def _challenge(self, a: Answer, msg: str, history):
        lang = a.language
        lines = []
        types = {f["type"] for f in a.flags}
        if "guaranteed_returns" in types:
            lines.append(T(lang, "No investment return can be guaranteed. Every estimate below is a probability "
                                 "distribution, and losses are always possible.",
                           "Καμία απόδοση δεν μπορεί να είναι εγγυημένη. Κάθε εκτίμηση παρακάτω είναι κατανομή "
                           "πιθανοτήτων και οι απώλειες είναι πάντα πιθανές."))
        if "authority_bias" in types:
            lines.append(T(lang, "An authority's opinion is not evidence I can verify. I evaluate the stock on "
                                 "the data, regardless of who holds which view.",
                           "Η γνώμη ενός «ειδικού» δεν είναι στοιχείο που μπορώ να επαληθεύσω. Αξιολογώ τη μετοχή με "
                           "βάση τα δεδομένα, όχι με βάση το ποιος λέει τι."))
        if "confirmation_bias" in types:
            lines.append(T(lang, "You asked me to confirm a view. I will present the evidence on both sides "
                                 "instead; the case against is deliberately included.",
                           "Ζητήσατε επιβεβαίωση μιας άποψης. Θα παρουσιάσω τα στοιχεία και των δύο πλευρών· "
                           "τα επιχειρήματα κατά περιλαμβάνονται σκόπιμα."))
        if "false_precision" in types:
            lines.append(T(lang, "An exact future price cannot be known. I give ranges with probabilities; a point "
                                 "estimate would be false precision.",
                           "Μια ακριβής μελλοντική τιμή δεν μπορεί να είναι γνωστή. Δίνω εύρη με πιθανότητες· "
                           "μια σημειακή εκτίμηση θα ήταν ψευδής ακρίβεια."))
        if "misleading_assumption" in types:
            lines.append(T(lang, "The premise contains an absolute ('always / must / never') that market history "
                                 "does not support: some stocks that fall never recover (survivorship bias hides them).",
                           "Η υπόθεση περιέχει ένα απόλυτο («πάντα / πρέπει / ποτέ») που η ιστορία των αγορών δεν "
                           "στηρίζει: κάποιες μετοχές που πέφτουν δεν ανακάμπτουν ποτέ (η μεροληψία επιβίωσης τις κρύβει)."))
        # numeric claims
        claims = guard.extract_claims(msg)
        if claims and a.tickers:
            st = self.tk.stock(a.tickers[0]) or {}
            facts = dict(st.get("fundamentals") or {})
            facts["price"] = st.get("price")
            facts["dividend_yield"] = (st.get("fundamentals") or {}).get("dividend_yield")
            checked = guard.verify_claims(claims, facts)
            a.claims = checked
            for c in checked:
                if c["status"] == "contradicted":
                    shown = (f"{c['actual'] * 100:.1f}%" if c["field"] in ("revenue_growth_yoy", "dividend_yield")
                             else money(c["actual"]) if c["field"] in ("price",)
                             else f"${c['actual'] / 1e9:,.1f}B" if c["field"] == "market_cap" else f"{c['actual']:.1f}")
                    lines.append(T(lang, f"Your figure '{c['text']}' does not match the verified data for {a.tickers[0]}: "
                                         f"{shown} (source: {st.get('fundamentals_source') or st.get('source')}). "
                                         "I use the verified figure.",
                                   f"Το στοιχείο «{c['text']}» δεν συμφωνεί με τα επαληθευμένα δεδομένα για {a.tickers[0]}: "
                                   f"{shown} (πηγή: {st.get('fundamentals_source') or st.get('source')}). Χρησιμοποιώ το επαληθευμένο."))
                    a.flags.append({"type": "fabricated_data", "match": c["text"]})
                elif c["status"] == "unverifiable":
                    lines.append(T(lang, f"I cannot verify '{c['text']}', so it is not used in this analysis.",
                                   f"Δεν μπορώ να επαληθεύσω το «{c['text']}», οπότε δεν χρησιμοποιείται."))
                else:
                    lines.append(T(lang, f"Your figure '{c['text']}' is consistent with the verified data.",
                                   f"Το στοιχείο «{c['text']}» συμφωνεί με τα επαληθευμένα δεδομένα."))
        a.add(T(lang, "Checking the premise", "Έλεγχος της υπόθεσης"), lines)

    def _repeat_check(self, a: Answer, msg: str, history):
        types = {f["type"] for f in a.flags}
        if not history or "pressure_without_evidence" not in types:
            return
        new_numbers = guard.NUM.search(msg) is not None and a.claims and any(c["status"] == "consistent" for c in a.claims)
        lang = a.language
        if not new_numbers:
            a.add(T(lang, "On changing my view", "Σχετικά με την αλλαγή άποψης"), [T(lang,
                  "Repeating the request does not change the evidence, so my assessment is unchanged. I will revise it "
                  "when new verifiable data arrives: prices, filings, guidance or a model update.",
                  "Η επανάληψη του αιτήματος δεν αλλάζει τα δεδομένα, άρα η εκτίμησή μου μένει ίδια. Θα την αναθεωρήσω "
                  "όταν υπάρξουν νέα επαληθεύσιμα στοιχεία: τιμές, καταθέσεις, guidance ή ενημέρωση μοντέλου.")])

    # ---------------------------------------------------------- stock
    def _stock(self, a: Answer, t: str, intents: list[str]):
        lang = a.language
        st = self.tk.stock(t)
        if not st:
            a.add(t, [T(lang, f"No verified data is available for {t}. I will not guess.",
                        f"Δεν υπάρχουν επαληθευμένα δεδομένα για {t}. Δεν θα μαντέψω.")])
            return
        fc = self.tk.forecast(t) or {}
        tech = st.get("technicals") or {}
        fu = st.get("fundamentals") or {}
        val = st.get("valuation") or {}
        src = st.get("source")
        a.ev(f"{t} price", st.get("price"), src, st.get("as_of"), "$")
        head = [T(lang, f"{st.get('name', t)} ({t}) last {money(st.get('price'))} as of {st.get('as_of')} "
                        f"(source: {src}); 1-day {pct(st.get('change_1d'))}, 1-month {pct(tech.get('ret_1m'))}, "
                        f"3-month {pct(tech.get('ret_3m'))}, 1-year {pct(tech.get('ret_1y'))}.",
                  f"{st.get('name', t)} ({t}) τελευταία τιμή {money(st.get('price'))} στις {st.get('as_of')} "
                  f"(πηγή: {src}); 1 ημέρα {pct(st.get('change_1d'))}, 1 μήνας {pct(tech.get('ret_1m'))}, "
                  f"3 μήνες {pct(tech.get('ret_3m'))}, 1 έτος {pct(tech.get('ret_1y'))}.")]
        a.add(f"{t} - " + T(lang, "facts", "δεδομένα"), head)
        pros, cons, neutral = [], [], []
        # technicals
        if tech.get("dist_sma200") is not None:
            (pros if tech["dist_sma200"] > 0 else cons).append(
                T(lang, f"Price is {pct(tech['dist_sma200'])} vs its 200-day average "
                        f"({'long-term uptrend' if tech['dist_sma200'] > 0 else 'long-term downtrend'}).",
                  f"Η τιμή είναι {pct(tech['dist_sma200'])} έναντι του 200ήμερου μέσου όρου "
                  f"({'μακροπρόθεσμη άνοδος' if tech['dist_sma200'] > 0 else 'μακροπρόθεσμη πτώση'})."))
        if tech.get("rsi14") is not None:
            r = tech["rsi14"]
            if r >= 70:
                cons.append(T(lang, f"RSI(14) {r:.0f}: overbought, so short-term pullback risk is elevated.",
                              f"RSI(14) {r:.0f}: υπεραγορασμένη, οπότε ο κίνδυνος βραχυπρόθεσμης διόρθωσης είναι αυξημένος."))
            elif r <= 30:
                pros.append(T(lang, f"RSI(14) {r:.0f}: oversold (mean-reversion potential, though oversold can stay oversold).",
                              f"RSI(14) {r:.0f}: υπερπουλημένη (πιθανή επαναφορά, αν και μπορεί να παραμείνει υπερπουλημένη)."))
        # fundamentals
        if fu.get("revenue_growth_yoy") is not None:
            (pros if fu["revenue_growth_yoy"] > 0.05 else cons).append(
                T(lang, f"TTM revenue growth {pct(fu['revenue_growth_yoy'])} YoY (SEC filings).",
                  f"Αύξηση εσόδων TTM {pct(fu['revenue_growth_yoy'])} σε ετήσια βάση (SEC)."))
        if fu.get("pe_ttm") is not None:
            pe = fu["pe_ttm"]
            (cons if pe > 35 else pros if pe < 15 else neutral).append(
                T(lang, f"Trailing P/E {pe:.1f}" + (f", forward P/E {fu['forward_pe']:.1f}" if fu.get("forward_pe") else "") + ".",
                  f"P/E (TTM) {pe:.1f}" + (f", forward P/E {fu['forward_pe']:.1f}" if fu.get("forward_pe") else "") + "."))
        if fu.get("fcf_yield") is not None:
            (pros if fu["fcf_yield"] > 0.04 else cons if fu["fcf_yield"] < 0.02 else neutral).append(
                T(lang, f"Free-cash-flow yield {pctu(fu['fcf_yield'])}.", f"Απόδοση ελεύθερων ταμειακών ροών {pctu(fu['fcf_yield'])}."))
        if fu.get("debt_to_equity") is not None and fu["debt_to_equity"] > 2:
            cons.append(T(lang, f"Debt/equity {fu['debt_to_equity']:.2f}: high leverage.", f"Χρέος/ίδια κεφάλαια {fu['debt_to_equity']:.2f}: υψηλή μόχλευση."))
        if val.get("implied_fcf_growth_10y") is not None:
            g, h = val["implied_fcf_growth_10y"], val.get("historical_revenue_cagr_3y")
            txt = T(lang, f"Reverse DCF: the price implies {pctu(g)} annual FCF growth for 10 years"
                          + (f" vs {pctu(h)} 3-year revenue CAGR." if h is not None else "."),
                    f"Αντίστροφο DCF: η τιμή προεξοφλεί {pctu(g)} ετήσια αύξηση FCF για 10 χρόνια"
                    + (f" έναντι {pctu(h)} 3ετούς CAGR εσόδων." if h is not None else "."))
            (cons if h is not None and g > h + 0.05 else pros if h is not None and g < h - 0.05 else neutral).append(txt)
        # forecast
        fh = (fc.get("horizons") or {}) if fc.get("available") else {}
        horizon = "30D" if "recovery" not in intents else "90D"
        f30 = fh.get(horizon)
        if f30:
            a.ev(f"{t} {horizon} P(up)", f30["p_up"], "prediction engine", fc.get("as_of"))
            line = T(lang, f"Model {horizon}: P(up) {pctu(f30['p_up'], 0)}, median {pct(f30['median_return'])}, "
                           f"80% range {money(f30['range_80'][0])}-{money(f30['range_80'][1])}, 5% VaR {pct(f30['var95'])}. "
                           f"Validated skill at this horizon: {f30['skill']}.",
                     f"Μοντέλο {horizon}: P(άνοδος) {pctu(f30['p_up'], 0)}, διάμεσος {pct(f30['median_return'])}, "
                     f"εύρος 80% {money(f30['range_80'][0])}-{money(f30['range_80'][1])}, VaR 5% {pct(f30['var95'])}. "
                     f"Επικυρωμένη ικανότητα σε αυτόν τον ορίζοντα: {f30['skill']}.")
            if f30["skill"] not in ("none-demonstrated", "unvalidated"):
                (pros if f30["p_up"] > 0.55 else cons if f30["p_up"] < 0.45 else neutral).append(line)
            else:
                a.add(T(lang, "Forecast (low confidence)", "Πρόβλεψη (χαμηλή αξιοπιστία)"), [line, T(lang,
                      "Walk-forward testing has NOT shown predictive skill beyond base rates here, so this is mostly a "
                      "volatility-scaled range, not a directional call.",
                      "Ο έλεγχος walk-forward ΔΕΝ έδειξε προβλεπτική ικανότητα πέρα από τις βασικές συχνότητες εδώ· "
                      "είναι κυρίως εύρος μεταβλητότητας, όχι κατευθυντική πρόβλεψη.")])
        # why moved
        if "why_moved" in intents:
            self._why(a, t, st)
        # news
        news = [n for n in (self.tk.news(t) or []) if not n.get("suspicious")][:4]
        sus = [n for n in (self.tk.news(t) or []) if n.get("suspicious")]
        if news:
            a.add(T(lang, "Recent headlines (sentiment is NOT in the forecast)", "Πρόσφατοι τίτλοι (το sentiment ΔΕΝ μπαίνει στην πρόβλεψη)"),
                  [f"{n['title']} ({n.get('publisher')}, {str(n.get('published_at'))[:10]}, score {n['sentiment']:+.2f})" for n in news])
        if sus:
            a.add(T(lang, "Excluded content", "Αποκλεισμένο περιεχόμενο"), [T(lang,
                  f"{len(sus)} news item(s) contained instruction-like text (possible prompt injection) and were ignored.",
                  f"{len(sus)} είδηση(-εις) περιείχαν κείμενο τύπου εντολής (πιθανή prompt injection) και αγνοήθηκαν.")])
            # never copy the offending text anywhere (it would reach the LLM prompt)
            a.flags.append({"type": "external_injection_blocked", "match": f"{len(sus)} news item(s) excluded"})
        a.add(T(lang, "Neutral / context", "Ουδέτερα στοιχεία"), neutral)
        a.add(T(lang, f"Case FOR {t}", f"Υπέρ {t}"), pros or [T(lang, "No strong supporting evidence in the data.", "Δεν υπάρχουν ισχυρά στοιχεία υπέρ.")])
        a.add(T(lang, f"Case AGAINST {t}", f"Κατά {t}"), cons or [T(lang, "No strong negative evidence in the data; unknown risks remain.", "Δεν υπάρχουν ισχυρά αρνητικά στοιχεία· άγνωστοι κίνδυνοι παραμένουν.")])
        balance = len(pros) - len(cons)
        a.stance = "mixed" if abs(balance) <= 1 else "constructive" if balance > 0 else "cautious"
        stance_txt = {"mixed": T(lang, "Mixed: the evidence points both ways.", "Μικτή: τα στοιχεία δείχνουν προς δύο κατευθύνσεις."),
                      "constructive": T(lang, "Constructive on balance, but not without risk.", "Θετική συνολικά, αλλά όχι χωρίς κίνδυνο."),
                      "cautious": T(lang, "Cautious on balance.", "Επιφυλακτική συνολικά.")}[a.stance]
        a.add(T(lang, "Assessment", "Εκτίμηση"), [stance_txt + " " + T(lang,
              "This is an analysis of data, not personal investment advice.",
              "Πρόκειται για ανάλυση δεδομένων, όχι προσωπική επενδυτική συμβουλή.")])
        a.add(T(lang, "What would change this view", "Τι θα άλλαζε αυτή την εκτίμηση"), self._change_view(lang, t, tech, fu, f30))

    def _why(self, a: Answer, t: str, st: dict):
        lang = a.language
        tech = st.get("technicals") or {}
        lines = []
        mkt = st.get("market_1d")
        if st.get("change_1d") is not None and mkt is not None:
            beta = st.get("beta") or 1.0
            explained = beta * mkt
            lines.append(T(lang, f"{t} moved {pct(st['change_1d'])} vs S&P 500 proxy {pct(mkt)}; with beta {beta:.2f}, "
                                 f"the market explains about {pct(explained)} and the stock-specific part is {pct(st['change_1d'] - explained)}.",
                           f"Η {t} κινήθηκε {pct(st['change_1d'])} έναντι {pct(mkt)} της αγοράς· με beta {beta:.2f}, "
                           f"η αγορά εξηγεί περίπου {pct(explained)} και το ειδικό μέρος είναι {pct(st['change_1d'] - explained)}."))
        if st.get("sector_1d") is not None:
            lines.append(T(lang, f"Sector ETF moved {pct(st['sector_1d'])} the same day.", f"Το ETF του κλάδου κινήθηκε {pct(st['sector_1d'])}."))
        if tech.get("rvol20") is not None:
            lines.append(T(lang, f"Volume was {tech['rvol20']:.1f}x its 20-day average"
                                 + (" (unusual, which suggests news-driven flow)." if tech["rvol20"] >= 2 else "."),
                           f"Ο όγκος ήταν {tech['rvol20']:.1f}x του 20ήμερου μέσου" + (" (ασυνήθιστος, πιθανή είδηση)." if tech["rvol20"] >= 2 else ".")))
        if st.get("recent_filings"):
            f = st["recent_filings"][0]
            lines.append(T(lang, f"Latest SEC filing: {f['form']} on {f['filing_date']}.", f"Τελευταία κατάθεση SEC: {f['form']} στις {f['filing_date']}."))
        lines.append(T(lang, "I can attribute the move statistically (market, sector, volume) but cannot verify a causal story "
                             "beyond the headlines shown.",
                       "Μπορώ να αποδώσω την κίνηση στατιστικά (αγορά, κλάδος, όγκος) αλλά όχι να επαληθεύσω αιτιώδη εξήγηση "
                       "πέρα από τους τίτλους που εμφανίζονται."))
        a.add(T(lang, "Why it moved (attribution)", "Γιατί κινήθηκε (απόδοση)"), lines)

    def _change_view(self, lang, t, tech, fu, f) -> list[str]:
        out = []
        if tech.get("support"):
            out.append(T(lang, f"A close below support at {money(tech['support'][0])} would weaken the technical case.",
                         f"Κλείσιμο κάτω από τη στήριξη {money(tech['support'][0])} θα αποδυνάμωνε το τεχνικό σκεπτικό."))
        if tech.get("resistance"):
            out.append(T(lang, f"A sustained break above resistance at {money(tech['resistance'][0])} would strengthen it.",
                         f"Διάσπαση της αντίστασης {money(tech['resistance'][0])} θα το ενίσχυε."))
        if fu.get("revenue_growth_yoy") is not None:
            out.append(T(lang, "The next 10-Q/10-K: a reversal in revenue growth or margins vs the trailing figures above.",
                         "Η επόμενη κατάθεση 10-Q/10-K: αντιστροφή στην αύξηση εσόδων ή στα περιθώρια."))
        out.append(T(lang, "A model update in which walk-forward skill becomes significant, or the forecast distribution shifts materially.",
                     "Ενημέρωση μοντέλου όπου η επικυρωμένη ικανότητα γίνεται σημαντική ή η κατανομή μετατοπίζεται ουσιαστικά."))
        out.append(T(lang, "A regime change (VIX spike, breadth collapse, credit spreads widening).",
                     "Αλλαγή καθεστώτος αγοράς (άνοδος VIX, κατάρρευση εύρους, διεύρυνση πιστωτικών spreads)."))
        return out

    # ---------------------------------------------------------- opportunities
    def _opportunities(self, a: Answer, risk_adjusted: bool):
        lang = a.language
        snap = self.tk.discovery()
        if not snap or not snap.get("rows"):
            a.add(T(lang, "Opportunities", "Ευκαιρίες"), [T(lang, "The discovery scan is not available yet; no ranking can be given.",
                                                               "Η σάρωση δεν είναι ακόμη διαθέσιμη· δεν μπορεί να δοθεί κατάταξη.")])
            return
        rows = snap["rows"]
        if risk_adjusted:
            def key(r):
                f = (r.get("forecast") or {}).get("90D")
                return (f["mu"] / f["sigma"]) if f and f.get("sigma") else -9
            ranked = sorted(rows, key=key, reverse=True)[:5]
            lines = []
            for r in ranked:
                f = r["forecast"]["90D"]
                lines.append(T(lang, f"{r['ticker']}: 90D expected/sigma {f['mu'] / f['sigma']:.2f}, P(up) {pctu(f['p_up'], 0)}, "
                                     f"63-day vol {pctu(r['vol63'], 0)}, max drawdown 1y {pct(r['max_dd_1y'])}.",
                               f"{r['ticker']}: 90Η αναμενόμενη/σίγμα {f['mu'] / f['sigma']:.2f}, P(άνοδος) {pctu(f['p_up'], 0)}, "
                               f"μεταβλητότητα {pctu(r['vol63'], 0)}, μέγ. πτώση 1 έτους {pct(r['max_dd_1y'])}."))
            a.add(T(lang, "Highest model risk-adjusted expectation (90D)", "Υψηλότερη αναμενόμενη απόδοση/ρίσκο μοντέλου (90Η)"), lines)
        else:
            ranked = sorted([r for r in rows if r["composite"]["medium"]["score"] is not None],
                            key=lambda r: r["composite"]["medium"]["score"], reverse=True)[:5]
            lines = [T(lang, f"{r['ticker']} ({r['sector']}): composite {r['composite']['medium']['score']:.0f}/100 - value "
                             f"{_n(r['factors']['value'])}, quality {_n(r['factors']['quality'])}, momentum {_n(r['factors']['momentum'])}.",
                       f"{r['ticker']} ({r['sector']}): σύνθετη {r['composite']['medium']['score']:.0f}/100 - αξία "
                       f"{_n(r['factors']['value'])}, ποιότητα {_n(r['factors']['quality'])}, ορμή {_n(r['factors']['momentum'])}.")
                     for r in ranked]
            a.add(T(lang, "Highest-scoring opportunities (medium horizon)", "Υψηλότερες βαθμολογίες (μεσοπρόθεσμα)"), lines)
        skill = self.tk.model_skill() or {}
        verdicts = {h: v.get("verdict") for h, v in skill.items()}
        a.add(T(lang, "Important caveats", "Σημαντικές επιφυλάξεις"), [
            T(lang, "Scores are relative rankings within a ~105-stock large-cap universe, not predictions of absolute returns.",
              "Οι βαθμολογίες είναι σχετική κατάταξη σε ~105 μεγάλες εταιρείες, όχι πρόβλεψη απόλυτης απόδοσης."),
            T(lang, f"Validated forecast skill (90D): {verdicts.get('90D') or 'not available'}",
              f"Επικυρωμένη ικανότητα πρόβλεψης (90Η): {verdicts.get('90D') or 'μη διαθέσιμη'}"),
            T(lang, "'Best' depends on your horizon, risk tolerance and existing holdings; concentration in one idea is risky.",
              "Το «καλύτερο» εξαρτάται από ορίζοντα, ανοχή κινδύνου και υπάρχουσες θέσεις· η συγκέντρωση σε μία ιδέα είναι επικίνδυνη."),
        ])
        a.stance = "ranking"

    # ---------------------------------------------------------- stress
    def _stress(self, a: Answer, msg: str):
        lang = a.language
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", msg)
        shock = -float(m.group(1)) / 100 if m else -0.10
        low = msg.lower()
        if re.search(r"(rise|rally|up|ανέβει|άνοδο)", low) and not re.search(r"(fall|drop|crash|down|πέσει|πτώση)", low):
            shock = abs(shock)
        factor = "QQQ" if re.search(r"nasdaq|qqq|νάσντακ|tech", low) else "SPY"
        res = self.tk.portfolio_stress(factor, shock)
        if not res:
            a.add(T(lang, "Stress test", "Stress test"), [T(lang, "No portfolio found. Add transactions in Portfolio first.",
                                                             "Δεν βρέθηκε χαρτοφυλάκιο. Προσθέστε συναλλαγές πρώτα.")])
            return
        cs = res.get("custom_shock") or {}
        a.ev("portfolio value", res["value"], "portfolio engine", None, res["base_currency"])
        lines = [T(lang, f"Portfolio '{res['portfolio']}' (value {res['value']:,.0f} {res['base_currency']}): if {factor} moves {pct(shock, 0)}, "
                         f"the beta model expects {pct(cs.get('expected_return'))} ({cs.get('expected_pnl', 0):,.0f} {res['base_currency']}), "
                         f"90% range {cs.get('range_90', [0, 0])[0]:,.0f} to {cs.get('range_90', [0, 0])[1]:,.0f} {res['base_currency']}.",
                   f"Χαρτοφυλάκιο «{res['portfolio']}» (αξία {res['value']:,.0f} {res['base_currency']}): αν ο {factor} κινηθεί {pct(shock, 0)}, "
                   f"το μοντέλο beta αναμένει {pct(cs.get('expected_return'))} ({cs.get('expected_pnl', 0):,.0f} {res['base_currency']}), "
                   f"εύρος 90% {cs.get('range_90', [0, 0])[0]:,.0f} έως {cs.get('range_90', [0, 0])[1]:,.0f} {res['base_currency']}.")]
        for p in sorted(cs.get("by_position", []), key=lambda x: x["expected_return"])[:6]:
            lines.append(f"{p['ticker']}: beta {p['beta']:.2f} -> {pct(p['expected_return'])}")
        lines.append(T(lang, "Betas are estimated on the last 252 sessions; in real crashes correlations rise, so losses can exceed this estimate.",
                       "Τα beta εκτιμώνται στις τελευταίες 252 συνεδριάσεις· σε πραγματικές κρίσεις οι συσχετίσεις αυξάνονται, οπότε οι απώλειες μπορεί να είναι μεγαλύτερες."))
        a.add(T(lang, "Stress test", "Stress test"), lines)
        a.stance = "scenario"

    # ---------------------------------------------------------- market
    def _market(self, a: Answer):
        lang = a.language
        rg = self.tk.regime()
        if not rg:
            a.add(T(lang, "Market", "Αγορά"), [T(lang, "Market regime data is unavailable right now.", "Τα δεδομένα αγοράς δεν είναι διαθέσιμα.")])
            return
        r, s = rg.get("regime") or {}, rg.get("sentiment") or {}
        lines = [T(lang, f"State: {r.get('state')} (score {r.get('score', 0):+.2f}); regime: {r.get('regime')}.",
                   f"Κατάσταση: {r.get('state')} (σκορ {r.get('score', 0):+.2f}); καθεστώς: {r.get('regime')}.")]
        for sg in (r.get("signals") or [])[:7]:
            lines.append(f"{sg['name']}: {sg['detail']}")
        if s.get("index") is not None:
            lines.append(T(lang, f"Risk Appetite Index {s['index']:.0f}/100 ({s['label']}).", f"Δείκτης Διάθεσης Κινδύνου {s['index']:.0f}/100 ({s['label']})."))
        mk = r.get("markov") or {}
        if mk.get("available"):
            lines.append(T(lang, f"Markov-switching model: probability of the high-volatility state {pctu(mk['p_high_vol_state'], 0)}.",
                           f"Μοντέλο Markov: πιθανότητα καθεστώτος υψηλής μεταβλητότητας {pctu(mk['p_high_vol_state'], 0)}."))
        a.add(T(lang, "Market regime", "Καθεστώς αγοράς"), lines)
        a.add(T(lang, "Limits", "Όρια"), [T(lang, "Regime labels describe current conditions; they are not forecasts of the next move.",
                                               "Οι ετικέτες περιγράφουν τις τρέχουσες συνθήκες· δεν προβλέπουν την επόμενη κίνηση.")])

    def _finish(self, a: Answer) -> dict:
        return {"answer": a.markdown(), "language": a.language, "intents": a.intents, "tickers": a.tickers,
                "flags": a.flags, "claims": a.claims, "stance": a.stance, "evidence": a.evidence,
                "sections": [{"title": t, "lines": ls} for t, ls in a.sections], "engine": "deterministic"}
