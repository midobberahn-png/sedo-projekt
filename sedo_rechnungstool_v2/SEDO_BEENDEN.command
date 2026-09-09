#!/bin/bash
# Beendet den lokalen SEDO-Server (Port 8000).
PIDS=$(lsof -ti tcp:8000)
if [ -n "$PIDS" ]; then
  kill $PIDS
  echo "SEDO-Server beendet."
else
  echo "Kein laufender SEDO-Server gefunden."
fi
sleep 2
