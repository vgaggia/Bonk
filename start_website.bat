@echo off
cd /d "%~dp0website"
if not exist node_modules (
    echo Installing website dependencies...
    npm install
)
npm start
