import json
import logging
from datetime import datetime

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 5000


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.vendor_id = self.scope['url_route']['kwargs']['vendor_id']
        self.group_name = f"chat_vendor_{self.vendor_id}"
        self.authenticated_user = None
        self.vendor_sender_name = None
        self.sender_type = None

        from urllib.parse import parse_qs
        query_string = self.scope.get('query_string', b'').decode()
        params_raw = parse_qs(query_string)
        params = {k: v[0] for k, v in params_raw.items()}

        jwt_token = params.get('token')
        chat_token = params.get('chat_token')

        if jwt_token:
            
            user = await self._get_user_from_jwt(jwt_token)
            if not user:
                logger.warning("ChatConsumer: invalid JWT on connect | vendor=%s", self.vendor_id)
                await self.close(code=4001)
                return

            if getattr(user, 'role', None) != 'officer':
                logger.warning(
                    "ChatConsumer: non-officer role denied | user=%s role=%s vendor=%s",
                    user.id, getattr(user, 'role', None), self.vendor_id
                )
                await self.close(code=4004)
                return

            vendor_ok = await self._vendor_belongs_to_org(self.vendor_id, user)
            if not vendor_ok:
                logger.warning(
                    "ChatConsumer: officer org mismatch | user=%s vendor=%s",
                    user.id, self.vendor_id
                )
                await self.close(code=4003)
                return

            self.authenticated_user = user
            self.sender_type = 'officer'
            logger.info("ChatConsumer: officer connected | user=%s vendor=%s", user.id, self.vendor_id)

        elif chat_token:
            
            token_obj = await self._get_valid_chat_token(chat_token, self.vendor_id)
            if not token_obj:
                logger.warning(
                    "ChatConsumer: invalid or expired chat token | vendor=%s token=%s",
                    self.vendor_id, chat_token
                )
                await self.close(code=4002)
                return

            self.vendor_sender_name = await self._get_vendor_name(self.vendor_id)
            self.sender_type = 'vendor'
            logger.info("ChatConsumer: vendor connected | vendor=%s", self.vendor_id)

        else:
            logger.warning("ChatConsumer: no auth provided | vendor=%s", self.vendor_id)
            await self.close(code=4000)
            return

        
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        
        await self.send(text_data=json.dumps({
            'type': 'connection_established',
            'sender_type': self.sender_type,
            'vendor_id': self.vendor_id,
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        logger.info(
            "ChatConsumer: disconnected | vendor=%s code=%s sender_type=%s",
            self.vendor_id, close_code, self.sender_type
        )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            logger.warning("ChatConsumer: invalid JSON received | vendor=%s", self.vendor_id)
            return

        content = data.get('content', '').strip()
        message_type = data.get('message_type', 'vendor_message')

        if not content:
            return

        if len(content) > MAX_MESSAGE_LENGTH:
            logger.warning(
                "ChatConsumer: message exceeds max length | vendor=%s len=%d",
                self.vendor_id, len(content)
            )
            return

        if message_type == 'internal_note' and self.sender_type != 'officer':
            logger.warning("ChatConsumer: vendor tried to send internal note — blocked")
            return

        message = await self._save_message(
            vendor_id=self.vendor_id,
            content=content,
            message_type=message_type,
            sender_type=self.sender_type,
            user=self.authenticated_user,
            vendor_sender_name=self.vendor_sender_name or '',
        )

        if not message:
            logger.error("ChatConsumer: failed to save message | vendor=%s", self.vendor_id)
            return

        payload = {
            'type': 'chat_message',
            'id': str(message['id']),
            'content': content,
            'message_type': message_type,
            'sender_type': self.sender_type,
            'sender_name': message['sender_name'],
            'created_at': message['created_at'],
            'vendor_id': self.vendor_id,
        }

    
        if message_type == 'internal_note':
            payload['internal'] = True

        await self.channel_layer.group_send(self.group_name, payload)

   
    async def chat_message(self, event):
       
        if event.get('internal') and self.sender_type == 'vendor':
            return

        await self.send(text_data=json.dumps({
            'type': 'chat_message',
            'id': event['id'],
            'content': event['content'],
            'message_type': event['message_type'],
            'sender_type': event['sender_type'],
            'sender_name': event['sender_name'],
            'created_at': event['created_at'],
        }))


    @database_sync_to_async
    def _get_user_from_jwt(self, token_string):
        try:
            from rest_framework_simplejwt.tokens import AccessToken
            from django.contrib.auth import get_user_model
            User = get_user_model()

            access_token = AccessToken(token_string)
            user_id = access_token['user_id']
            return User.objects.get(id=user_id)
        except Exception as exc:
            logger.debug("_get_user_from_jwt failed: %s", str(exc))
            return None

    @database_sync_to_async
    def _vendor_belongs_to_org(self, vendor_id, user):
        try:
            from vendors.models import Vendor
            return Vendor.objects.filter(
                id=vendor_id,
                organization=user.organization
            ).exists()
        except Exception:
            return False

    @database_sync_to_async
    def _get_valid_chat_token(self, token_string, vendor_id):
        try:
            from communication.models import ChatToken
            token = ChatToken.objects.get(
                token=token_string,
                vendor__id=vendor_id,
            )
            if not token.is_valid:
                return None
            
            if not token.otp_verified:
                logger.warning(
                    "_get_valid_chat_token: OTP not verified yet | vendor=%s", vendor_id
                )
                return None
            
            return token
        
        except ChatToken.DoesNotExist:
            return None
        except Exception as exc:
            logger.exception("_get_valid_chat_token error: %s", str(exc))
            return None

    @database_sync_to_async
    def _get_vendor_name(self, vendor_id):
        try:
            from vendors.models import Vendor
            vendor = Vendor.objects.get(id=vendor_id)
            return vendor.name
        except Exception:
            return 'Vendor'

    @database_sync_to_async
    def _save_message(self, vendor_id, content, message_type, sender_type, user, vendor_sender_name):
        try:
            from communication.models import Message
            from vendors.models import Vendor

            vendor = Vendor.objects.get(id=vendor_id)

            msg = Message.objects.create(
                vendor=vendor,
                message_type=message_type,
                sender_type=sender_type,
                sender=user if sender_type == 'officer' else None,
                vendor_sender_name=vendor_sender_name if sender_type == 'vendor' else '',
                content=content,
            )

            sender_name = (
                (user.full_name or user.email) if sender_type == 'officer'
                else (vendor_sender_name or vendor.name)
            )

            return {
                'id': str(msg.id),
                'sender_name': sender_name,
                'created_at': msg.created_at.isoformat(),
            }
        except Exception as exc:
            logger.exception("_save_message error: %s", str(exc))
            return None