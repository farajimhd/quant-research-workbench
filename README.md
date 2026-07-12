# Quant Research Workbench

This project is moving to a local-first research workflow. The goal is to develop, inspect, and improve momentum strategies on local historical data before translating anything to QuantConnect or live execution.



## Back Test



## Strategy Configuration





## Frontend

The frontend is a React/Vite operator UI served by the FastAPI backend. Streamlit has been removed so the UI can use the same design stance and component model as the larger trading dashboard.

Install Python dependencies:

```powershell
pip install -r requirements.txt
```

Install frontend dependencies:

```powershell
npm --prefix frontend install
```

Run the backend API:

```powershell
.\scripts\run_backend.ps1
```

Run the React development server:

```powershell
npm --prefix frontend run dev
```

For a production-style local build:

```powershell
npm --prefix frontend run build
.\scripts\run_backend.ps1 -NoReload
```







