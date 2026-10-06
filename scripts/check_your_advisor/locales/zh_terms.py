"""Chinese translations: short labels, codes and joins that several sections share."""

MESSAGES = {
    # Joins that sit between two values.
    " to ": " 至 ",
    " vs ": " 对 ",
    "{id} ({name})": "{id}（{name}）",
    "{label}: {unavailable}": "{label}：{unavailable}",
    # Separators between translated items: a list, and the notes on a year's count.
    ", ": "、",
    "; ": "；",
    "  ({notes})": "（{notes}）",
    # Section 19: the year span after a cluster's record count.
    ", {low}": "，{low}",
    ", {low}–{high}": "，{low}–{high}",
    # Section 13's collapsed list when the report carries no count for it.
    "All record titles, verbatim, by year": "全部记录的题名，原样列出，按年份排列",
    # `theses.name_script`: the writing system a name is in.
    "han": "汉字",
    "latin": "拉丁字母",
    "mixed": "混合",
    "empty": "空",
    # `theses.DEGREE_VALUES`: the two degrees a thesis export is normalised to.
    "master": "硕士",
    "doctoral": "博士",
}
