import { useEffect, useRef, useState } from 'react';
import type { GameState } from './types';

interface UseGameReturn {
  state: GameState | null;
  error: string | null;
  reconnecting: boolean;
  send: (op: string, data?: Record<string, unknown>) => void;
}

export function useGame(channelId: string, playerId: string): UseGameReturn {
  const [state, setState] = useState<GameState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);

  useEffect(() => {
    unmountedRef.current = false;

    function connect() {
      if (unmountedRef.current) return;
      const url = `/api/ws/${channelId}?player_id=${encodeURIComponent(playerId)}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        const msg: { op: string; d: unknown } = JSON.parse(event.data as string);
        if (msg.op === 'state') {
          setState(msg.d as GameState);
          setError(null);
          setReconnecting(false);
        } else if (msg.op === 'error') {
          setError((msg.d as { message: string }).message);
        }
        // player_joined / player_left: state.connected_players already reflects this
      };

      ws.onerror = () => {};

      ws.onclose = () => {
        if (unmountedRef.current) return;
        setReconnecting(true);
        timerRef.current = setTimeout(connect, 2000);
      };
    }

    connect();

    return () => {
      unmountedRef.current = true;
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [channelId, playerId]);

  const send = (op: string, data: Record<string, unknown> = {}) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ op, d: data }));
    }
  };

  return { state, error, reconnecting, send };
}
