"""Ticker-specific tree classifier and held-out temporal calibration."""
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss,confusion_matrix,balanced_accuracy_score
from .config import CONTRACT


def train(x,y):
    model=HistGradientBoostingClassifier(**CONTRACT['classifier'])
    model.fit(x,y)
    if list(model.classes_)!=[0,1,2,3]:raise ValueError('Training partition lacks one or more outcome classes')
    return model


def calibrate(model,x,y):
    calibration=LogisticRegression(C=1.,max_iter=200,random_state=17)
    calibration.fit(np.log(np.clip(model.predict_proba(x),1e-8,1)),y)
    if list(calibration.classes_)!=[0,1,2,3]:raise ValueError('Calibration partition lacks outcome classes')
    return calibration


def predict(model,calibration,x):
    probabilities=model.predict_proba(x)
    return calibration.predict_proba(np.log(np.clip(probabilities,1e-8,1))) if calibration is not None else probabilities


def metrics(y,p):
    y=np.asarray(y);pred=p.argmax(axis=1)
    result=dict(rows=len(y),class_counts=np.bincount(y,minlength=4).tolist(),
                log_loss=float(log_loss(y,p,labels=[0,1,2,3])),
                brier=float(np.mean(np.sum((p-np.eye(4)[y])**2,axis=1))),
                accuracy=float(np.mean(y==pred)),balanced_accuracy=float(balanced_accuracy_score(y,pred)),
                confusion_matrix=confusion_matrix(y,pred,labels=[0,1,2,3]).tolist())
    mask=(y==1)|(y==2)
    if mask.any():
        reaction=p[mask,1:3];reaction/=reaction.sum(axis=1,keepdims=True)
        result['resolved_contact_log_loss']=float(log_loss(y[mask]-1,reaction,labels=[0,1]))
    return result
