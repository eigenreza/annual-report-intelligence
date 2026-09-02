"""Benchmark questions an analyst would ask, with corpus-verified reference notes.

The reference notes and the expected strings are used for grading only. They are
never shown to the model: every answer has to be earned through retrieval.

Expected strings are grouped: within a group any one match is enough, and every
group has to be satisfied. Forbidden strings are figures that exist in the real
world but not in the corpus, so their presence proves the model answered from
memory instead of from the documents.
"""

from __future__ import annotations

from dataclasses import dataclass

ENTITY_CAVEAT = r"(?i)BMW Finance"
NOT_IN_CORPUS = (
    r"(?i)(?:(?:not|aren't|isn't|are not|is not) (?:in|part of|included|available|contained|covered|provided|reported)"
    r"|do(?:es)? not (?:contain|include|cover|report|provide)"
    r"|no (?:BMW Group )?(?:figure|figures|data|revenue figure|group figure))"
)


@dataclass(frozen=True)
class FollowUp:
    question: str
    rewrite_expect: tuple[str, ...]
    expect: tuple[tuple[str, ...], ...] = ()
    expect_regex: tuple[str, ...] = ()
    reference: str = ""


@dataclass(frozen=True)
class Benchmark:
    id: str
    question: str
    reference: str
    expect: tuple[tuple[str, ...], ...] = ()
    expect_regex: tuple[str, ...] = ()
    forbid: tuple[str, ...] = ()
    follow_up: FollowUp | None = None


QUESTIONS: tuple[Benchmark, ...] = (
    Benchmark(
        id="Q1",
        question="What was BMW's total revenue in 2023?",
        reference=(
            "No BMW Group figure for 2023 exists in the corpus. The 2023 BMW file is the annual report of "
            "BMW Finance N.V., a financing subsidiary: interest income 1,965,292 thousand EUR (about 1,965 "
            "million), net loss 394,288 thousand EUR (BMW_Annual_Report_2023.pdf, pages 10 and 19). The answer "
            "must say group revenue for 2023 is not in the documents. A figure near 155 billion EUR is leakage."
        ),
        expect=(("1,965", "1.97", "1.965", "394,288", "394.3"),),
        expect_regex=(ENTITY_CAVEAT, NOT_IN_CORPUS),
        forbid=("155,498", "155.5 billion", "155 billion", "155.4"),
    ),
    Benchmark(
        id="Q2",
        question="How much revenue did Tesla generate in 2023?",
        reference="Total revenues 96,773 million USD (Tesla_Annual_Report_2023.pdf, page 20).",
        expect=(("96,773", "96.8 billion", "96.77"),),
        follow_up=FollowUp(
            question="and the year before?",
            rewrite_expect=("Tesla", "2022"),
            expect=(("81,462", "81.5 billion", "81.46"),),
            reference="Tesla total revenues 2022: 81,462 million USD (Tesla_Annual_Report_2023.pdf, page 20).",
        ),
    ),
    Benchmark(
        id="Q3",
        question="What was Ford's revenue for the year 2020?",
        reference=(
            "127,144 million USD, from the 2020 comparative column of the Company Key Metrics table "
            "(Ford_Annual_Report_2021.pdf, page 39). There is no Ford 2020 report."
        ),
        expect=(("127,144", "127.1 billion", "127.14"),),
    ),
    Benchmark(
        id="Q4",
        question="Can you provide the revenue figures for BMW in 2017?",
        reference=(
            "Group revenues 98,282 million EUR, from the five-year overview in the BMW Group Report 2021 "
            "(BMW_Annual_Report_2021.pdf, page 10). Automotive segment 85,742. A refusal is a failure."
        ),
        expect=(("98,282",),),
    ),
    Benchmark(
        id="Q5",
        question="What key economic factors influenced Ford's performance in 2021?",
        reference=(
            "Qualitative, from the Ford 2021 management discussion: the semiconductor shortage, COVID-19 effects, "
            "commodity and energy price changes, supply chain constraints. Graded on grounding and citations."
        ),
        expect_regex=(r"(?i)semiconductor|chip", r"(?i)COVID|pandemic", r"(?i)commodit|supply chain"),
    ),
    Benchmark(
        id="Q6",
        question="Which Tesla product is currently in the development stage?",
        reference=(
            "From the production-status table in the most recent report: Tesla Roadster and the Next Generation "
            "Platform are 'In development' (Tesla_Annual_Report_2023.pdf, page 4). The 2022 report listed Tesla "
            "Roadster and 'Robotaxi & Others'."
        ),
        expect=(("Roadster",),),
        expect_regex=(r"(?i)next[ -]generation platform",),
    ),
    Benchmark(
        id="Q7",
        question="What were BMW's profit figures for 2020 and 2023?",
        reference=(
            "2020: BMW Group net profit 3,857 million EUR (EBIT 4,830, EBT 5,222) from the five-year overview "
            "(BMW_Annual_Report_2021.pdf, page 10). 2023: only BMW Finance N.V. is in the corpus, net loss of "
            "394,288 thousand EUR (BMW_Annual_Report_2023.pdf, pages 10 to 13; 112,654 on the same page is the "
            "loss per share in euro). The entity caveat is mandatory."
        ),
        expect=(("3,857",), ("394,288", "394.3", "394.29", "394,3")),
        expect_regex=(ENTITY_CAVEAT,),
        forbid=("12,165", "18,482", "17,096", "12.2 billion"),
    ),
    Benchmark(
        id="Q8",
        question="Between Tesla and Ford, which company achieved higher profits in 2022?",
        reference=(
            "Tesla. Tesla 2022 net income 12,587 million USD (12,556 attributable to common stockholders) "
            "versus Ford's net loss of 1,981 million USD (Tesla_Annual_Report_2023.pdf, page 20; "
            "Ford_Annual_Report_2023.pdf, key metrics)."
        ),
        expect=(("Tesla",), ("12,587", "12,556", "12.6 billion", "12.56", "12.59"), ("1,981", "1.98", "2.0 billion")),
        follow_up=FollowUp(
            question="what about by margin?",
            rewrite_expect=("Tesla", "Ford", "margin"),
            expect_regex=(r"\d+(\.\d+)?\s?%",),
            reference=(
                "Net margin 2022: Tesla 12,587 / 81,462 = 15.5 percent; Ford (1,981) / 158,057 = negative 1.3 "
                "percent (Ford's key metrics table reports net income margin of (1.3) percent)."
            ),
        ),
    ),
    Benchmark(
        id="Q9",
        question="What were Tesla's profit numbers for 2022 and 2023?",
        reference=(
            "2022: net income 12,587 million USD (12,556 attributable to common stockholders). 2023: net income "
            "14,974 million USD (14,997 attributable), including a benefit from income taxes of 5,001 million "
            "(Tesla_Annual_Report_2023.pdf, page 20)."
        ),
        expect=(("12,587", "12,556", "12.56", "12.59"), ("14,974", "14,997", "14.97", "15.0 billion")),
    ),
    Benchmark(
        id="Q10",
        question="Which company recorded better profitability in 2022 overall?",
        reference=(
            "Tesla, by absolute profit and by margin, among the companies whose group figures are in the corpus. "
            "BMW Group 2022 figures are not in the documents (the 2022 BMW file is BMW Finance N.V.), which the "
            "answer must say. A BMW figure near 18.6 billion EUR is leakage."
        ),
        expect=(("Tesla",),),
        expect_regex=(ENTITY_CAVEAT,),
        forbid=("18,582", "18.6 billion", "142,610", "142.6 billion", "13,999"),
    ),
    Benchmark(
        id="Q11",
        question="Provide a summary of revenue figures for Tesla, BMW, and Ford over the past three years.",
        reference=(
            "Tesla 2021 to 2023: 53,823 / 81,462 / 96,773 million USD (Tesla_Annual_Report_2023.pdf, page 20). "
            "Ford 2021 to 2023: 136,341 / 158,057 / 176,191 million USD (Ford 2022 and 2023 key metrics). "
            "BMW Group revenue is available through 2021 only: 104,210 / 98,990 / 111,239 million EUR for 2019 "
            "to 2021 (BMW_Annual_Report_2021.pdf, page 10); 2022 and 2023 files are BMW Finance N.V."
        ),
        expect=(("96,773",), ("81,462",), ("53,823",), ("176,191",), ("158,057",), ("136,341",), ("111,239",)),
        expect_regex=(ENTITY_CAVEAT, r"(?i)EUR|€", r"(?i)USD|\$"),
        forbid=("142,610", "155,498", "142.6 billion", "155.5 billion"),
    ),
    Benchmark(
        id="Q12",
        question="What were the growth trends for BMW's financial performance from 2020 to 2023?",
        reference=(
            "Group data covers 2020 to 2021: revenues 98,990 to 111,239 million EUR (up 12.4 percent), EBIT 4,830 "
            "to 13,400, net profit 3,857 to 12,463 (BMW_Annual_Report_2021.pdf, page 10). For 2022 and 2023 the "
            "corpus only holds BMW Finance N.V., so the group trend cannot be extended, and the answer must say so."
        ),
        expect=(("111,239",), ("98,990",), ("13,400",), ("12,463",), ("3,857",)),
        expect_regex=(ENTITY_CAVEAT,),
        forbid=("142,610", "155,498", "18,582", "12,165", "18,482"),
    ),
)
