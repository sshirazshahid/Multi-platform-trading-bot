"""
hmm_regime.py - Hidden Markov regime detection (pure NumPy, no heavy deps).

A Gaussian HMM fit with Baum-Welch (EM) in log-space + Viterbi decoding, on
features [log-return, realized volatility]. Replaces 'emotion'/discretionary
regime calls with a statistical latent-state model. States are auto-labeled
(TREND_UP / TREND_DOWN / RANGE / HIGH_VOL) by their fitted mean-return & vol.

Live use: filter_proba() gives the causal posterior over states at the last bar
(uses only past+current data -> no look-ahead).
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _logsumexp(a, axis=None):
    m = np.max(a, axis=axis, keepdims=True)
    s = m + np.log(np.sum(np.exp(a - m), axis=axis, keepdims=True))
    return np.squeeze(s, axis=axis) if axis is not None else float(s)


class GaussianHMM:
    def __init__(self, n_states=3, n_iter=60, tol=1e-4, seed=0):
        self.K = n_states; self.n_iter = n_iter; self.tol = tol; self.rng = np.random.default_rng(seed)

    def _emission_logprob(self, X):
        T, D = X.shape
        logB = np.zeros((T, self.K))
        for k in range(self.K):
            var = np.maximum(self.vars_[k], 1e-6)
            diff = X - self.means_[k]
            logB[:, k] = -0.5 * (np.sum(diff * diff / var, axis=1) + np.sum(np.log(2 * np.pi * var)))
        return logB

    def _init(self, X):
        T, D = X.shape
        # init means by quantiles of the first feature dimension, vars = global var
        order = np.argsort(X[:, 0])
        chunks = np.array_split(order, self.K)
        self.means_ = np.array([X[c].mean(axis=0) for c in chunks])
        self.vars_ = np.tile(X.var(axis=0) + 1e-6, (self.K, 1))
        self.startprob_ = np.full(self.K, 1.0 / self.K)
        self.transmat_ = np.full((self.K, self.K), 0.1 / (self.K - 1))
        np.fill_diagonal(self.transmat_, 0.9)

    def fit(self, X):
        X = np.asarray(X, dtype=float)
        self._init(X)
        T = len(X); prev_ll = -np.inf
        for _ in range(self.n_iter):
            logB = self._emission_logprob(X)
            logA = np.log(self.transmat_ + 1e-300); logpi = np.log(self.startprob_ + 1e-300)
            # forward
            la = np.zeros((T, self.K)); la[0] = logpi + logB[0]
            for t in range(1, T):
                la[t] = logB[t] + _logsumexp(la[t-1][:, None] + logA, axis=0)
            ll = _logsumexp(la[-1])
            # backward
            lb = np.zeros((T, self.K))
            for t in range(T - 2, -1, -1):
                lb[t] = _logsumexp(logA + logB[t+1][None, :] + lb[t+1][None, :], axis=1)
            log_gamma = la + lb - ll
            gamma = np.exp(log_gamma - _logsumexp(log_gamma, axis=1)[:, None])
            # xi sum
            xi = np.zeros((self.K, self.K))
            for t in range(T - 1):
                m = la[t][:, None] + logA + logB[t+1][None, :] + lb[t+1][None, :] - ll
                xi += np.exp(m)
            # re-estimate
            self.startprob_ = gamma[0] / gamma[0].sum()
            self.transmat_ = xi / np.maximum(xi.sum(axis=1, keepdims=True), 1e-300)
            gs = np.maximum(gamma.sum(axis=0), 1e-300)
            self.means_ = (gamma.T @ X) / gs[:, None]
            for k in range(self.K):
                diff = X - self.means_[k]
                self.vars_[k] = (gamma[:, k][:, None] * diff * diff).sum(axis=0) / gs[k] + 1e-6
            if abs(ll - prev_ll) < self.tol:
                break
            prev_ll = ll
        self.loglik_ = ll
        return self

    def viterbi(self, X):
        X = np.asarray(X, float); T = len(X)
        logB = self._emission_logprob(X); logA = np.log(self.transmat_ + 1e-300)
        delta = np.zeros((T, self.K)); psi = np.zeros((T, self.K), int)
        delta[0] = np.log(self.startprob_ + 1e-300) + logB[0]
        for t in range(1, T):
            v = delta[t-1][:, None] + logA
            psi[t] = np.argmax(v, axis=0); delta[t] = logB[t] + np.max(v, axis=0)
        path = np.zeros(T, int); path[-1] = np.argmax(delta[-1])
        for t in range(T - 2, -1, -1):
            path[t] = psi[t+1, path[t+1]]
        return path

    def filter_proba(self, X):
        """Causal posterior over states at the LAST bar (forward filtering only)."""
        X = np.asarray(X, float); T = len(X)
        logB = self._emission_logprob(X); logA = np.log(self.transmat_ + 1e-300)
        la = np.log(self.startprob_ + 1e-300) + logB[0]
        for t in range(1, T):
            la = logB[t] + _logsumexp(la[:, None] + logA, axis=0)
        post = np.exp(la - _logsumexp(la))
        return post


def _label_states(hmm, ret_mean, ret_std):
    """Map latent states -> regime names using de-standardized mean return & vol."""
    info = {}
    mr = hmm.means_[:, 0] * ret_std + ret_mean      # mean return (destandardized)
    vol = hmm.means_[:, 1]                            # vol feature (standardized scale ok for ranking)
    hi_vol = int(np.argmax(vol)); lo_ret = int(np.argmin(mr)); hi_ret = int(np.argmax(mr))
    labels = {}
    for k in range(hmm.K):
        if k == hi_vol and vol[k] > vol.mean() + 0.5 * vol.std():
            labels[k] = "HIGH_VOL"
        elif k == hi_ret and mr[k] > 0:
            labels[k] = "TREND_UP"
        elif k == lo_ret and mr[k] < 0:
            labels[k] = "TREND_DOWN"
        else:
            labels[k] = "RANGE"
        info[k] = {"mean_ret": float(mr[k]), "vol_feat": float(vol[k]),
                   "persistence": float(hmm.transmat_[k, k])}
    return labels, info


def detect_regime(df: pd.DataFrame, n_states=3, vol_window=12) -> dict:
    """Fit an HMM on (return, rolling-vol) and return the CURRENT (causal) regime."""
    if df is None or len(df) < 120:
        return {"label": "UNKNOWN", "note": "insufficient_history"}
    c = df["close"].astype(float)
    ret = np.log(c / c.shift(1))
    vol = ret.rolling(vol_window).std()
    feat = pd.DataFrame({"ret": ret, "vol": vol}).dropna()
    if len(feat) < 100:
        return {"label": "UNKNOWN", "note": "insufficient_features"}
    rmean, rstd = feat["ret"].mean(), feat["ret"].std() + 1e-12
    X = np.column_stack([(feat["ret"] - rmean) / rstd,
                         (feat["vol"] - feat["vol"].mean()) / (feat["vol"].std() + 1e-12)])
    hmm = GaussianHMM(n_states=n_states).fit(X)
    labels, info = _label_states(hmm, rmean, rstd)
    post = hmm.filter_proba(X)              # causal posterior at last bar
    cur = int(np.argmax(post))
    return {"label": labels[cur], "state": cur, "confidence": round(float(post[cur]), 3),
            "posterior": {labels[k]: round(float(post[k]), 3) for k in range(hmm.K)},
            "persistence": round(float(hmm.transmat_[cur, cur]), 3),
            "states": {labels[k]: info[k] for k in range(hmm.K)}, "loglik": round(float(hmm.loglik_), 1)}
