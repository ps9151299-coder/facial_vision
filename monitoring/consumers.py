import json
from channels.generic.websocket import AsyncWebsocketConsumer

class ProctorAlertConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs'].get('session_id', 'global')
        self.group_name = f"proctor_session_{self.session_id}"

        # Join the session broadcast group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        # Also join global broadcast group
        await self.channel_layer.group_add(
            "proctor_global",
            self.channel_name
        )

        await self.accept()
        await self.send(text_data=json.dumps({
            'type': 'connection_established',
            'message': f'Connected to real-time proctor alert bus for session: {self.session_id}'
        }))

    async def disconnect(self, close_code):
        # Leave groups
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )
        await self.channel_layer.group_discard(
            "proctor_global",
            self.channel_name
        )

    async def receive(self, text_data):
        """Receive message from proctor client (e.g. ping, review acknowledgment)."""
        try:
            data = json.loads(text_data)
            msg_type = data.get('type')
            if msg_type == 'ping':
                await self.send(text_data=json.dumps({'type': 'pong'}))
        except Exception:
            pass

    async def violation_alert(self, event):
        """Send incident alert to WebSocket client."""
        await self.send(text_data=json.dumps({
            'type': 'violation_alert',
            'data': event['data']
        }))

    async def telemetry_update(self, event):
        """Send live student head pose / status metrics to WebSocket client."""
        await self.send(text_data=json.dumps({
            'type': 'telemetry_update',
            'data': event['data']
        }))
