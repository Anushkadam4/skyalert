import { useCallback, useEffect, useRef, useState } from "react";
import { liveSocketUrl } from "./api.js";

const MAX_ALERTS = 400;
const MAX_MATCHES = 100;

/** Keeps a WebSocket open to /ws with exponential-backoff reconnects.
 *  Incoming alerts are buffered and flushed to React state 4x per second,
 *  so a fast stream doesn't re-render the page on every message. */
export function useLiveSocket(token) {
  const [connected, setConnected] = useState(false);
  const [alerts, setAlerts] = useState([]);
  const [matches, setMatches] = useState([]);
  const [received, setReceived] = useState(0);
  const [unseenMatches, setUnseen] = useState(0);
  const buffer = useRef({ alerts: [], matches: [] });

  useEffect(() => {
    if (!token) return undefined;
    let ws;
    let retry = 0;
    let closed = false;
    let timer;

    const connect = () => {
      ws = new WebSocket(liveSocketUrl(token));
      ws.onopen = () => {
        retry = 0;
        setConnected(true);
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "alert") buffer.current.alerts.push(msg.data);
        else if (msg.type === "match") buffer.current.matches.push(msg.data);
      };
      ws.onclose = (ev) => {
        setConnected(false);
        if (closed || ev.code === 4401) return;
        const delay = Math.min(30000, 500 * 2 ** retry++) * (0.5 + Math.random() / 2);
        timer = setTimeout(connect, delay);
      };
    };
    connect();

    const flush = setInterval(() => {
      // newest first; copy out of the buffer before the state updaters run
      const a = [...buffer.current.alerts].reverse();
      const m = [...buffer.current.matches].reverse();
      buffer.current = { alerts: [], matches: [] };
      if (a.length) {
        setAlerts((prev) => [...a, ...prev].slice(0, MAX_ALERTS));
        setReceived((n) => n + a.length);
      }
      if (m.length) {
        setMatches((prev) => [...m, ...prev].slice(0, MAX_MATCHES));
        setUnseen((n) => n + m.length);
      }
    }, 250);

    return () => {
      closed = true;
      clearTimeout(timer);
      clearInterval(flush);
      ws?.close();
    };
  }, [token]);

  const markSeen = useCallback(() => setUnseen(0), []);
  return { connected, alerts, matches, received, unseenMatches, markSeen };
}
