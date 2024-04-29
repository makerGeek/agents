import asyncio
import logging
from enum import Enum
from typing import Annotated

from livekit import rtc
from livekit.agents import (
    JobContext,
    JobRequest,
    WorkerOptions,
    cli,
    llm,
)
from livekit.agents.voice_assistant import AssistantContext, VoiceAssistant
from livekit.plugins import deepgram, elevenlabs, openai, silero


class Room(Enum):
    BEDROOM = "bedroom"
    LIVING_ROOM = "living room"
    KITCHEN = "kitchen"
    BATHROOM = "bathroom"
    OFFICE = "office"


class AssistantFnc(llm.FunctionContext):
    @llm.ai_callable(desc="Turn on/off the lights in a room")
    async def toggle_light(
        self,
        room: Annotated[Room, llm.TypeInfo(desc="The specific room")],
        status: bool,
    ):
        logging.info("toggle_light %s %s", room, status)
        ctx = AssistantContext.get_current()
        key = "enabled_rooms" if status else "disabled_rooms"
        li = ctx.get_metadata(key, [])
        li.append(room)
        ctx.store_metadata(key, li)

    @llm.ai_callable(desc="User want the assistant to stop/pause speaking")
    def stop_speaking(self):
        pass  # do nothing


async def entrypoint(ctx: JobContext):
    gpt = openai.LLM(model="gpt-4-turbo")

    assistant = VoiceAssistant(
        vad=silero.VAD(),
        stt=deepgram.STT(),
        llm=gpt,
        tts=elevenlabs.TTS(),
        fnc_ctx=AssistantFnc(),
    )

    @assistant.on("agent_speech_interrupted")
    def _agent_speech_interrupted(chat_ctx: llm.ChatContext, msg: llm.ChatMessage):
        msg.text += "... (user interrupted you)"

    @assistant.on("function_calls_done")
    def _function_calls_done(ctx: AssistantContext):
        logging.info("function_calls_done %s", ctx)
        enabled_rooms = ctx.get_metadata("enabled_rooms", [])
        disabled_rooms = ctx.get_metadata("disabled_rooms", [])

        async def _handle_answer():
            prompt = "Make a summary of the following actions you did:"
            if enabled_rooms:
                enabled_rooms_str = ", ".join(enabled_rooms)
                prompt += f"\n - You enabled the lights in the following rooms: {enabled_rooms_str}"

            if disabled_rooms:
                disabled_rooms_str = ", ".join(disabled_rooms)
                prompt += f"\n - You disabled the lights in the following rooms {disabled_rooms_str}"

            chat_ctx = llm.ChatContext(
                messages=[llm.ChatMessage(role=llm.ChatRole.SYSTEM, text=prompt)]
            )

            stream = await gpt.chat(chat_ctx)
            await assistant.say(stream)

        if enabled_rooms or disabled_rooms:
            asyncio.ensure_future(_handle_answer())

    # start the assistant with the first participant found inside the room

    @ctx.room.on("participant_connected")
    def _on_participant_connected(participant: rtc.RemoteParticipant):
        assistant.start(ctx.room, participant)

    for participant in ctx.room.participants.values():
        assistant.start(ctx.room, participant)
        break

    await asyncio.sleep(1)
    await assistant.say("Hey, how can I help you today?")


async def request_fnc(req: JobRequest) -> None:
    logging.info("received request %s", req)
    await req.accept(entrypoint)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(request_fnc))
