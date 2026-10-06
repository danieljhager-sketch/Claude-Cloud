"""Loads and checks config.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

DEFAULT_MODEL = "claude-opus-5-5"


class Applicant(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""
    work_authorization: str = ""
    needs_sponsorship: bool | None = None
    willing_to_relocate: bool | None = None
    earliest_start_date: str = ""
    salary_expectation: str = ""
    # Answers to other questions you expect to see, e.g. "How did you hear about us?".
    extra_answers: dict[str, str] = Field(default_factory=dict)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class Search(BaseModel):
    roles: list[str] = Field(default_factory=list)
    exclude_title_words: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_only: bool = False
    greenhouse: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)
    ashby: list[str] = Field(default_factory=list)
    remotive_search: list[str] = Field(default_factory=list)
    discover_with_web_search: bool = False
    max_web_searches: int = 8

    @field_validator("greenhouse", "lever", "ashby")
    @classmethod
    def _lowercase(cls, names: list[str]) -> list[str]:
        return [n.strip().lower() for n in names if n and n.strip()]


class Matching(BaseModel):
    min_score: int = 70
    max_jobs_to_score: int = 40


class Apply(BaseModel):
    max_applications_per_run: int = 5
    require_approval: bool = True
    headless: bool = False
    write_cover_letter: bool = True


class ClaudeSettings(BaseModel):
    model: str = DEFAULT_MODEL


class Config(BaseModel):
    applicant: Applicant
    resume_file: Path
    search: Search = Field(default_factory=Search)
    matching: Matching = Field(default_factory=Matching)
    apply: Apply = Field(default_factory=Apply)
    claude: ClaudeSettings = Field(default_factory=ClaudeSettings)

    # Set by load_config: the folder config.yaml lives in. Relative paths are resolved from here.
    base_dir: Path = Path(".")

    @property
    def resume_path(self) -> Path:
        path = self.resume_file.expanduser()
        return path if path.is_absolute() else self.base_dir / path

    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    @property
    def applications_dir(self) -> Path:
        return self.base_dir / "applications"


class ConfigError(Exception):
    pass


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"No config file at {path}. Run `python -m job_agent init` to create one."
        )
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    try:
        config = Config(**raw, base_dir=path.resolve().parent)
    except ValidationError as exc:
        raise ConfigError(f"{path} has a problem:\n{exc}") from exc
    if not config.resume_path.exists():
        raise ConfigError(
            f"Resume not found at {config.resume_path}. Put your resume there or fix `resume_file`."
        )
    if config.resume_path.suffix.lower() not in (".pdf", ".txt", ".md"):
        raise ConfigError("resume_file must be a .pdf, .txt or .md file.")
    return config
