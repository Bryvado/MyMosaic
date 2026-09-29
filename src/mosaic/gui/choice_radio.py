"""Native radio choices and mode-specific help text."""
import dearpygui.dearpygui as dpg

RADIO_HELP = {
    "opportunity": {
        "Overall": "Combined Electoral Opportunity penalty. Lower is better.",
        "Rating": "Each group's opportunity credit relative to its reference. Higher is better.",
        "Opportunity count": "Modeled full and partial opportunity districts for each group. "
                             "Dashed lines show the group references.",
    },
    "severance": {
        "Overall": "Combined Neighborhood Severance penalty. Lower is better.",
        "By group": "Share of each group's weighted neighborhood kept together. Higher is better.",
    },
    "dispersion": {
        "Overall": "Combined Community Dispersion penalty. Lower is better.",
        "By group": "How little each group is spread across districts, after a population-based "
                    "allowance. Higher is better.",
    },
    "ratchet": {
        "Off": "Keep the population tolerance fixed throughout the run.",
        "Standard": "Tighten tolerance when a new best plan is found, without excluding that plan.",
        "Strict": "Tighten tolerance at every eligible step, without excluding the current plan.",
    },
    "focus": {
        "All residents": "Measure reference-district continuity using all residents.",
        "Republican": "Measure reference-district continuity using Republican voters.",
        "Democratic": "Measure reference-district continuity using Democratic voters.",
    },
    "gap_mode": {
        "Robust (recommended)": "Average Efficiency Gap across modeled election conditions.",
        "Static": "Calculate Efficiency Gap directly from the loaded vote totals.",
    },
    "direction": {
        "Fair": "Move this partisan metric toward zero.",
        "D": "Favor the Democratic direction of this metric.",
        "R": "Favor the Republican direction of this metric.",
    },
    "seats": {
        "D": "Favor more Democratic seats.",
        "R": "Favor more Republican seats.",
    },
}


def choice_radio(*, items, default_value, help_key, horizontal=True, callback=None):
    """Use one native radio group so both selection and rendering are exclusive."""
    return dpg.add_radio_button(
        items=items, default_value=default_value, horizontal=horizontal,
        callback=callback, user_data=RADIO_HELP[help_key],
    )
