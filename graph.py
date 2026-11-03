"""
LANGGRAPH WORKFLOW: wires the agents together.

   START -> patient_agent -> risk_agent --+--> CRITICAL / HIGH -> emergency_agent -> clinician_agent -> caregiver_agent -> finalize
                                          +--> MEDIUM ---------------------------> clinician_agent -> caregiver_agent -> wellness_agent -> finalize
                                          +--> LOW ---------------------------------------------------------------------> wellness_agent -> finalize
"""
import operator
from typing import Annotated, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

import agents


class CareState(TypedDict, total=False):
    # inputs
    patient_id: str
    user_message: str
    location: Optional[tuple]          # (lat, lon) from the phone/GPS, if available
    # produced by agents
    reply: str
    extracted: dict
    risk: dict
    emergency: dict
    clinician_summary: str
    caregiver_message: str
    wellness_tips: str
    final_reply: str
    trace: Annotated[list, operator.add]   # every agent appends; lists are concatenated, not overwritten


def route_after_risk(state: CareState) -> str:
    level = state["risk"]["level"]
    if level in ("CRITICAL", "HIGH"):
        return "emergency_agent"
    if level == "MEDIUM":
        return "clinician_agent"
    return "wellness_agent"


def route_after_caregiver(state: CareState) -> str:
    return "wellness_agent" if state["risk"]["level"] == "MEDIUM" else "finalize"


def build_graph():
    g = StateGraph(CareState)
    g.add_node("patient_agent", agents.patient_agent)
    g.add_node("risk_agent", agents.risk_agent)
    g.add_node("emergency_agent", agents.emergency_agent)
    g.add_node("clinician_agent", agents.clinician_agent)
    g.add_node("caregiver_agent", agents.caregiver_agent)
    g.add_node("wellness_agent", agents.wellness_agent)
    g.add_node("finalize", agents.finalize)

    g.add_edge(START, "patient_agent")
    g.add_edge("patient_agent", "risk_agent")
    g.add_conditional_edges("risk_agent", route_after_risk,
                            {"emergency_agent": "emergency_agent", "clinician_agent": "clinician_agent",
                             "wellness_agent": "wellness_agent"})
    g.add_edge("emergency_agent", "clinician_agent")
    g.add_edge("clinician_agent", "caregiver_agent")
    g.add_conditional_edges("caregiver_agent", route_after_caregiver,
                            {"wellness_agent": "wellness_agent", "finalize": "finalize"})
    g.add_edge("wellness_agent", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def run_turn(app, patient_id: str, message: str, location=None) -> dict:
    return app.invoke({"patient_id": patient_id, "user_message": message, "location": location, "trace": []})
