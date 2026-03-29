from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum


class ArchitectureComponentType(str, Enum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    DATABASE = "database"
    EXTERNAL_API = "external_api"
    INFRASTRUCTURE = "infrastructure"


class TechStackItem(BaseModel):
    name: str = Field(..., description="Name of the technology")
    category: str = Field(..., description="Category (e.g., Backend, Frontend, Database)")
    reason: str = Field(..., description="Why this technology is chosen")
    version: Optional[str] = Field(None, description="Recommended version")


class ArchitectureComponent(BaseModel):
    name: str
    type: ArchitectureComponentType
    description: str
    responsibilities: List[str]
    technologies: List[str]


class WorkflowStep(BaseModel):
    step_number: int
    title: str
    description: str
    components_involved: List[str]
    key_actions: List[str]


class PrerequisiteItem(BaseModel):
    category: str  # Knowledge, Tool, Infrastructure, etc.
    items: List[str]


class SolutionApproach(BaseModel):
    name: str
    description: str
    pros: List[str]
    cons: List[str]
    complexity: str  # Simple, Medium, Complex
    estimated_time: str  # Development time estimate
    best_for: str  # When to use this approach


class RealWorldExample(BaseModel):
    title: str
    description: str
    company: str
    link: Optional[str]
    lessons_learned: List[str]


class LearningReference(BaseModel):
    title: str
    url: str
    type: str  # Tutorial, Documentation, Guide, Course, etc.
    difficulty: str  # Beginner, Intermediate, Advanced


class ProjectBlueprint(BaseModel):
    project_name: str
    description: str
    problem_statement: str
    system_architecture: List[ArchitectureComponent]
    tech_stack: List[TechStackItem]
    workflow: List[WorkflowStep]
    prerequisites: List[PrerequisiteItem]
    solution_approaches: List[SolutionApproach]
    real_world_examples: List[RealWorldExample]
    learning_references: List[LearningReference]
    timeline: Dict[str, str]
    estimated_budget: Optional[str]
    next_steps: List[str]


class UserMessage(BaseModel):
    problem_statement: str = Field(..., description="The problem or project idea to analyze")
    context: Optional[str] = Field(None, description="Additional context about the project")


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatResponse(BaseModel):
    message: ChatMessage
    blueprint: Optional[ProjectBlueprint] = None
