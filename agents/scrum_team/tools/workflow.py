# agents/scrum_team/tools/workflow.py
from ..state import ScrumState

def generate_workflow_diagram(state: ScrumState) -> str:
    """
    Generates a PlantUML diagram of the current workflow.
    """
    plantuml_code = """
@startuml
title Current Workflow

start

:User Request;

fork
    :ProductOwner;
    note right
        - Refine requirements
        - Create/update stories
        - Prioritize backlog
    end note
fork again
    :ScrumMaster;
    note left
        - Facilitate events
        - Remove impediments
        - Monitor budget
    end note
fork again
    :DevTeam;
    note left
        - Implement stories
        - Create pull requests
        - Run tests
    end note
fork again
    :QA;
    note right
        - Review pull requests
        - Propose test cases
    end note
fork again
    :Architect;
    note right
        - Review pull requests
        - Propose ADRs
    end note
fork again
    :QualityGuardian;
    note right
        - Calculate KPIs
        - Update sprint report
    end note
end fork

:Sprint Review;
note right
    - Demonstrate increment
    - Gather feedback
end note

:Sprint Retrospective;
note left
    - Discuss what went well
    - Discuss what could be improved
end note

stop

@enduml
"""
    with open("docs/workflow.puml", "w") as f:
        f.write(plantuml_code)
    return "Workflow diagram generated at docs/workflow.puml"

def gather_workflow_improvement_proposals(state: ScrumState) -> list:
    """
    Gathers proposals for workflow improvements.
    """
    # In a real implementation, this would involve analyzing the current
    # workflow and project requirements to suggest improvements.
    # For now, we'll return some dummy proposals.
    return [
        "Proposal 1: Automate the release process to reduce manual effort.",
        "Proposal 2: Introduce a formal code review checklist to improve code quality.",
        "Proposal 3: Implement a continuous integration pipeline to catch integration issues early.",
    ]
