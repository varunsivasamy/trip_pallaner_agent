from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .bootstrap import build_orchestrator
from .config import Settings
from .evaluation import EvaluationReport, evaluate_trip
from .models import TripState

_HIDDEN_FIELDS = {"places", "user_messages"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    async with httpx.AsyncClient(timeout=settings.http_timeout_s) as http:
        app.state.orchestrator = build_orchestrator(settings, http)
        app.state.trips = {}
        yield


app = FastAPI(title="Trip Planner", lifespan=lifespan)


class UserMessage(BaseModel):
    message: str = Field(min_length=1)


async def _advance(request: Request, state: TripState, message: str) -> TripState:
    state.add_user_message(message)
    return await request.app.state.orchestrator.run(state)


@app.post("/trips", response_model=TripState, response_model_exclude=_HIDDEN_FIELDS)
async def create_trip(body: UserMessage, request: Request) -> TripState:
    state = TripState()
    request.app.state.trips[state.trip_id] = state
    return await _advance(request, state, body.message)


@app.post("/trips/{trip_id}/messages", response_model=TripState, response_model_exclude=_HIDDEN_FIELDS)
async def send_message(trip_id: str, body: UserMessage, request: Request) -> TripState:
    return await _advance(request, _get_trip(request, trip_id), body.message)


@app.get("/trips/{trip_id}", response_model=TripState, response_model_exclude=_HIDDEN_FIELDS)
async def get_trip(trip_id: str, request: Request) -> TripState:
    return _get_trip(request, trip_id)


class BookingConfirmation(BaseModel):
    confirm: bool = True


@app.post("/trips/{trip_id}/confirm-booking", response_model=TripState, response_model_exclude=_HIDDEN_FIELDS)
async def confirm_trip_booking(trip_id: str, body: BookingConfirmation, request: Request) -> TripState:
    state = _get_trip(request, trip_id)
    if body.confirm:
        state.confirm_booking()
    else:
        state.cancel_booking()
    return await request.app.state.orchestrator.run(state)


@app.get("/trips/{trip_id}/evaluate", response_model=EvaluationReport)
async def evaluate_trip_plan(trip_id: str, request: Request) -> EvaluationReport:
    state = _get_trip(request, trip_id)
    return await evaluate_trip(state)


def _get_trip(request: Request, trip_id: str) -> TripState:
    state = request.app.state.trips.get(trip_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown trip_id")
    return state
