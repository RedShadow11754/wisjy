"""
Core services for the UK Wind Procurement FastAPI Backend:
  - cutoff_advisor.py     : Evaluates the 8 Day-1 cutoffs and recommends 07:30, 11:30, 15:30, 18:30 UTC
  - feature_extractor.py  : Queries PostgreSQL up to cutoff timestamp & builds the (24, 9) feature matrix
  - model_predictor.py    : Loads wind_procurement_model.pkl & runs vectorized 24-slot inference
  - accuracy_estimator.py : Computes leak-free Ex-Ante Expected Accuracy %, P10/P50/P90, & Realized Audit
"""
