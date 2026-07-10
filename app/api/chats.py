from fastapi import APIRouter, Request, Response, Form, Depends, HTTPException, BackgroundTasks, status
from fastapi.responses import JSONResponse
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from app.utils.logger import logger
from app.db.redis_chat import create_chat, get_chat, update_chat_conversations, fetch_user_chats_from_db, delete_chat
from app.db.redis_file import get_pending_files, clear_pending_files, get_file_by_s3_key
from app.db.redis_models import ConversationTurn, RedisChat, format_file_for_api
from app.dependencies import verify_authenticated_session, get_authorized_chat, get_file_manager
from app.models import (
    ChatCreateUpdateResponse, ChatSummary, ProcessChatRequest, 
    LLMErrorResponse, User,
    AppendAssistantTurnRequest
)
from app.services.file_manager import FileManager
from app.utils.format_helpers import format_conversation_turns_for_api

router = APIRouter()

@router.post("/c", response_model=ChatCreateUpdateResponse)
async def send_message_to_chat(
    request: Request,
    response: Response,
    prompt: Optional[str] = Form(None),
    chat_id: Optional[str] = Form(None),
    current_user: User = Depends(verify_authenticated_session) 
):
    logger.debug(f"[send_message_to_chat] Received POST to /c. chat_id from form: '{chat_id}', prompt: '{bool(prompt)}'")
    user_id = current_user.user_id
    initial_chat_id_from_form = chat_id
    current_chat_id = chat_id
    conversations = []

    if current_chat_id:
        logger.debug(f"[send_message_to_chat] Current chat_id exists: '{current_chat_id}'. Verifying ownership for user '{user_id}'.")
        chat = await get_chat(current_chat_id)
        if not chat or chat.user_id != user_id:
            logger.warning(f"[send_message_to_chat] Chat '{current_chat_id}' not found or user '{user_id}' mismatch. Creating new chat.")
            new_chat = await create_chat(user_id)
            current_chat_id = new_chat.chat_id
            logger.info(f"[send_message_to_chat] New chat '{current_chat_id}' created. Original chat_id from form was '{initial_chat_id_from_form}'.")
        else:
            logger.debug(f"[send_message_to_chat] Chat '{current_chat_id}' verified for user '{user_id}'. Using existing conversations.")
            conversations = chat.conversations
    else:
        logger.info(f"[send_message_to_chat] No chat_id provided in form. Creating new chat for user '{user_id}'.")
        new_chat = await create_chat(user_id)
        current_chat_id = new_chat.chat_id
        logger.info(f"[send_message_to_chat] New chat '{current_chat_id}' created.")

    logger.debug(f"[send_message_to_chat] Effective chat_id for this operation: '{current_chat_id}'.")
    pending_files_records = await get_pending_files(current_chat_id)
    logger.debug(f"[send_message_to_chat] Called get_pending_files for chat_id '{current_chat_id}'. Found {len(pending_files_records)} pending file(s).")
    
    detailed_input_files = [
        db_file_dict for file_record in pending_files_records
        if (db_file_dict := format_file_for_api(file_record)) is not None
    ]

    if prompt or pending_files_records:
        logger.debug(f"[send_message_to_chat] Condition met (prompt or pending files exist). Proceeding to create user turn and clear pending files for chat_id '{current_chat_id}'.")
        user_turn = ConversationTurn(
            role="user",
            prompt=prompt or "",
            input_files=detailed_input_files
        )
        conversations.append(user_turn)

        await clear_pending_files(current_chat_id)
        logger.debug(f"[send_message_to_chat] Called clear_pending_files for chat_id '{current_chat_id}'.")

        try:
            await update_chat_conversations(current_chat_id, conversations)
        except Exception as e:
            logger.error(f"Failed to update chat conversations for {current_chat_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to save conversation update.")

    return ChatCreateUpdateResponse(
        message="Chat message processed successfully",
        chat_id=current_chat_id
    )

@router.get("/c", response_model=List[ChatSummary])
async def list_user_chats(
    response: Response,
    current_user: User = Depends(verify_authenticated_session)
):
    chats = await fetch_user_chats_from_db(current_user.user_id)
    return [
        ChatSummary(
            chat_id=chat.chat_id,
            user_id=chat.user_id,
            created_at=chat.created_at.isoformat(),
            updated_at=chat.updated_at.isoformat(),
            chat_name=chat.chat_name,
        ) for chat in chats
    ]

@router.get("/c/{chat_id}")
async def get_specific_chat(
    response: Response,
    chat: RedisChat = Depends(get_authorized_chat)
) -> Dict[str, Any]:
    try:
        conversations_list = await format_conversation_turns_for_api(chat.conversations)
        response_dict = {
            "chat_id": chat.chat_id,
            "user_id": chat.user_id,
            "created_at": chat.created_at.isoformat(),
            "updated_at": chat.updated_at.isoformat(),
            "chat_name": chat.chat_name,
            "conversations": conversations_list
        }
        
        if response_dict["chat_name"] is None:
            del response_dict["chat_name"]

        return response_dict
    except Exception as e:
        logger.error(f"Error preparing chat data for response {chat.chat_id}: {e}")
        raise HTTPException(status_code=500, detail="Error preparing chat data for response.")

@router.post(
    "/c/{chat_id}/process",
    responses={500: {"model": LLMErrorResponse}}
)
async def process_chat(
    request: Request,
    response: Response,
    request_data: ProcessChatRequest,
    background_tasks: BackgroundTasks,
    chat: RedisChat = Depends(get_authorized_chat)
) -> Dict[str, Any]:
    chat_id = chat.chat_id
    llm = request.app.state.llm_orchestrator
    
    if not llm:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LLM service is not available."
        )
    
    try:
        # llm.process_command now starts a background task and returns task info
        task_info = await llm.process_command(
            conversations=chat.conversations, # Use chat.conversations directly
            chat_id=chat_id,
            background_tasks=background_tasks
        )
        
        # Return the task ID and initial status to the client
        return task_info

    except Exception as e:
        logger.error(f"Error initiating LLM task for chat {chat_id}: {e}", exc_info=True)
        # This exception is for errors during the *scheduling* of the task
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=LLMErrorResponse(
                detail="An error occurred while initiating the LLM processing task.", 
                error_details=str(e)
            ).model_dump()
        )

@router.get("/c/task/{task_id}/status")
async def get_chat_task_status(
    request: Request,
    task_id: str,
    current_user: User = Depends(verify_authenticated_session) 
):
    llm = request.app.state.llm_orchestrator
    if not llm:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM service is not available."
        )

    status_data = await llm.get_task_status(task_id)

    if status_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID '{task_id}' not found or has expired."
        )
    
    if status_data and (chat_id_from_task := status_data.get("chat_id_associated_with_task")):
        try:
            task_chat_owner = await get_chat(chat_id_from_task)
            if not task_chat_owner or task_chat_owner.user_id != current_user.user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to view this task's result."
                )
        except HTTPException: 
            raise
        except Exception as e:
            logger.error(f"Error during task ownership check for task {task_id}, chat {chat_id_from_task}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not verify task ownership."
            )

    return status_data

@router.post("/c/{chat_id}/assistant_turn", response_model=Dict[str, Any])
async def append_assistant_turn_to_chat(
    request: Request,
    request_data: AppendAssistantTurnRequest,
    chat: RedisChat = Depends(get_authorized_chat)
):
    chat_id = chat.chat_id
    assistant_turn_dict = request_data.assistant_turn_data

    try:
        # Reconstruct the ConversationTurn object
        # Ensure timestamp is handled correctly if it's a string
        if 'timestamp' in assistant_turn_dict and isinstance(assistant_turn_dict['timestamp'], str):
            try:
                # Attempt to parse with timezone, then without if it fails
                assistant_turn_dict['timestamp'] = datetime.fromisoformat(assistant_turn_dict['timestamp'])
            except ValueError:
                assistant_turn_dict['timestamp'] = datetime.strptime(assistant_turn_dict['timestamp'], "%Y-%m-%dT%H:%M:%S.%f")

        elif 'timestamp' not in assistant_turn_dict:
             assistant_turn_dict['timestamp'] = datetime.now(timezone.utc) # Default if missing

        # Ensure api_costs is a dict
        if 'api_costs' not in assistant_turn_dict or not isinstance(assistant_turn_dict.get('api_costs'), dict):
            assistant_turn_dict['api_costs'] = {}

        # Validate output_files structure
        if 'output_files' in assistant_turn_dict and assistant_turn_dict['output_files'] is not None:
            valid_output_files = []
            if isinstance(assistant_turn_dict['output_files'], list):
                for f_dict in assistant_turn_dict['output_files']:
                    # Check if it's a dictionary and has essential keys like 'file_id' or 's3_key'
                    if isinstance(f_dict, dict) and ('file_id' in f_dict or 's3_key' in f_dict):
                        valid_output_files.append(f_dict)
                    else:
                        logger.warning(f"Skipping malformed file data in output_files for chat {chat_id}: {f_dict}")
            assistant_turn_dict['output_files'] = valid_output_files
        else:
            assistant_turn_dict['output_files'] = []

        # response should be a dict
        if 'response' not in assistant_turn_dict or not isinstance(assistant_turn_dict.get('response'), dict):
             assistant_turn_dict['response'] = {"message": "Assistant response data missing or malformed."}
        
        # Ensure role is assistant
        assistant_turn_dict['role'] = 'assistant'
        
        # Prompt is optional for assistant
        if 'prompt' not in assistant_turn_dict:
            assistant_turn_dict['prompt'] = None

        # Input files are typically empty for assistant response turn, but ensure it's a list if present
        if 'input_files' not in assistant_turn_dict or not isinstance(assistant_turn_dict.get('input_files'), list):
            assistant_turn_dict['input_files'] = []

        assistant_turn = ConversationTurn(**assistant_turn_dict)
        
        chat.conversations.append(assistant_turn)
        
        updated_chat = await update_chat_conversations(chat_id, chat.conversations)
        if not updated_chat:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save assistant turn to conversation."
            )
        
        final_conversations_list = await format_conversation_turns_for_api(updated_chat.conversations)
        return {
            "message": "Assistant turn appended successfully.",
            "conversations": final_conversations_list
        }

    except Exception as e:
        logger.error(f"Error appending assistant turn for chat {chat_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process and append assistant turn: {str(e)}"
        )

@router.delete("/c/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_endpoint(
    chat_id: str,
    response: Response,
    current_user: User = Depends(verify_authenticated_session),
    file_manager: FileManager = Depends(get_file_manager)
):
    success = await delete_chat(current_user.user_id, chat_id, file_manager)
    if not success:
        existing_chat = await get_chat(chat_id)
        if not existing_chat:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Failed to delete chat"
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)