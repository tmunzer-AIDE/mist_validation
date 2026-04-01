#!/bin/bash

cd frontend/
ng build --deploy-url static/
cp dist/browser/browser/* ../backend/app/static
#cp dist/browser/browser/index.html ../backend/app/templates
cd ..