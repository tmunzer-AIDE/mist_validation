#!/bin/bash

cd frontend/
ng build --deploy-url static/
cp dist/browser/browser/* ../backend/frontend/static
cp dist/browser/browser/index.html ../backend/app/frontend
cd ..