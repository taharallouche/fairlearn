from __future__ import annotations

import logging
from typing import Literal

import numpy as np
import pandas as pd
from scipy.optimize import OptimizeResult, minimize
from scipy.spatial.distance import cdist
from scipy.special import softmax
from sklearn.base import (
    BaseEstimator,
    ClassifierMixin,
    TransformerMixin,
    check_is_fitted,
)
from sklearn.calibration import LabelEncoder
from sklearn.dummy import check_random_state
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.utils.multiclass import type_of_target

from fairlearn.utils._fixes import validate_data
from fairlearn.utils._input_validation import _validate_and_reformat_input

LOGGER = logging.getLogger(__name__)


class FairRepresentationLearner(ClassifierMixin, TransformerMixin, BaseEstimator):
    r"""
    A transformer and classifier that learns a latent representation of the input data to
    obfuscate the sensitive features while preserving the classification and reconstruction
    performance.

    The model minimizes a loss function that consists of three terms: the reconstruction error,
    the classification error, and the statistical-parity error.

    Read more in the :ref:`User Guide <preprocessing>`.

    Parameters
    ----------
    n_prototypes : int, default=2
        Number of prototypes to use in the latent representation.

    Ax : float, default=1.0
        Weight for the reconstruction error term in the objective function.

    Ay : float, default=1.0
        Weight for the classification error term in the objective function.

    Az : float, default=1.0
        Weight for the fairness error term in the objective function.

    random_state : int, np.random.RandomState, or None, default=None
        Seed or random number generator for reproducibility.

    optimizer : Literal["L-BFGS-B", "Nelder-Mead", "Powell", "SLSQP", "TNC", "trust-constr",
                        "COBYLA", "COBYQA"], default="L-BFGS-B"
        Optimization algorithm to use for minimizing the objective function.

    tol : float, default=1e-6
        Convergence tolerance for the optimization algorithm.

    max_iter : int, default=1000
        Maximum number of iterations for the optimization algorithm.

    Attributes
    ----------
    n_prototypes : int
        Number of prototypes to use in the latent representation.

    Ax : float
        Weight for the reconstruction error term in the objective function.

    Ay : float
        Weight for the classification error term in the objective function.

    Az : float
        Weight for the fairness error term in the objective function.

    random_state : int, np.random.RandomState, or None
        Seed or random number generator for reproducibility.

    optimizer : Literal["L-BFGS-B", "Nelder-Mead", "Powell", "SLSQP", "TNC", "trust-constr",
                        "COBYLA", "COBYQA"]
        Optimization algorithm to use for minimizing the objective function.

    tol : float
        Tolerance for the optimization algorithm.

    max_iter : int
        Maximum number of iterations for the optimization algorithm.

    coef_ : np.ndarray
        Coefficients of the learned model.

    n_iter_ : int
        Number of iterations run by the optimization algorithm.

    n_features_in_ : int
        Number of features in the input data.

    classes_ : np.ndarray
        Unique classes in the target variable.

    _label_encoder : LabelEncoder
        Encoder for transforming labels to numeric values.

    _groups : pd.Series
        Unique sensitive feature groups.

    _prototypes_ : np.ndarray or None
        Learned prototypes, if sensitive features were provided.

    _alpha : np.ndarray or None
        Learned dimension weights, if sensitive features were provided.

    _prototype_dim : int or None
        Dimension of each prototype vector.

    _latent_mapping_size : int or None
        Size of the latent representation mapping.

    _prototype_predictions_size : int or None
        Size of the prototype predictions vector.

    _prototype_vectors_size : int or None
        Total size of the prototype vectors.

    _optimizer_size : int or None
        Total size of the optimizer variables.

    _fall_back_classifier : LogisticRegression or None
        Fallback classifier used when no sensitive features are provided.

    Methods
    -------
    __init__(self, n_prototypes=2, Ax=1.0, Ay=1.0, Az=1.0, random_state=None, optimizer="L-BFGS-B",
             tol=1e-6, max_iter=1000)
        Initializes the FairRepresentationLearner with the given parameters.

    fit(self, X, y, *, sensitive_features=None)
        Fits the model to the input data X and target variable y. Optionally uses sensitive features.

    _optimize_with_sensitive_features(self, X, y, sensitive_features, random_state)
        Optimizes the model with sensitive features.

    _optimize_without_sensitive_features(self, X, y, random_state)
        Optimizes the model without sensitive features using a fallback classifier.

    transform(self, X) -> np.ndarray
        Transforms the input data X to the learned latent representation.

    predict_proba(self, X) -> np.ndarray
        Predicts class probabilities for the input data X.

    predict(self, X) -> np.ndarray
        Predicts class labels for the input data X.

    prototypes_(self) -> np.ndarray
        Returns the learned prototypes.

    alpha_(self) -> np.ndarray
        Returns the learned dimension weights.

    _get_latent_mapping(X, prototypes, dimension_weights) -> np.ndarray
        Computes the latent representation mapping for the input data X.

    _validate_X_y(self, X, y) -> tuple[np.ndarray, np.ndarray]
        Validates the input data X and target variable y.

    __sklearn_tags__(self)
        Returns the scikit-learn tags for the estimator.

    Notes
    -----
    The FairRepresentationLearner implements the algorithms intoduced in Zemel et al.
    :footcite:`pmlr-v28-zemel13`.

    If no sensitive features are provided during fitting, the model falls back to a Logistic
    Regression classifier.

    References
    ----------
    .. footbibliography::

    Examples
    --------
    >>> import numpy as np
    >>> from fairlearn.preprocessing import FairRepresentationLearner
    >>> X = np.array([[0, 1], [1, 0], [0, 0], [1, 1]])
    >>> y = np.array([0, 1, 0, 1])
    >>> sensitive_features = np.array([0, 0, 1, 1])
    >>> frl = FairRepresentationLearner(n_prototypes=2, random_state=42)
    >>> frl.fit(X, y, sensitive_features=sensitive_features)
    >>> X_transformed = frl.transform(X)
    >>> y_pred = frl.predict(X)
    """

    n_prototypes: int
    Ax: float
    Ay: float
    Az: float
    random_state: int | np.random.RandomState | None
    optimizer: Literal[
        "L-BFGS-B", "Nelder-Mead", "Powell", "SLSQP", "TNC", "trust-constr", "COBYLA", "COBYQA"
    ]
    tol: float
    max_iter: int
    coef_: np.ndarray
    n_iter_: int
    n_features_in_: int
    classes_: np.ndarray
    _label_encoder: LabelEncoder
    _groups: pd.Series
    # The following attributes are set during fitting and can be None depending on whether
    # sensitive features were provided
    _prototypes_: np.ndarray | None
    _alpha: np.ndarray | None
    _prototype_dim: int | None
    _latent_mapping_size: int | None
    _prototype_predictions_size: int | None
    _prototype_vectors_size: int | None
    _optimizer_size: int | None
    _fall_back_classifier: LogisticRegression | None

    def __init__(
        self,
        n_prototypes: int = 2,
        Ax: float = 1.0,
        Ay: float = 1.0,
        Az: float = 1.0,
        random_state: int | np.random.RandomState | None = None,
        optimizer: Literal[
            "L-BFGS-B", "Nelder-Mead", "Powell", "SLSQP", "TNC", "trust-constr", "COBYLA", "COBYQA"
        ] = "L-BFGS-B",
        tol: float = 1e-6,
        max_iter: int = 1000,
    ) -> None:
        self.n_prototypes = n_prototypes
        self.Az = Az
        self.Ax = Ax
        self.Ay = Ay
        self.random_state = random_state
        self.optimizer = optimizer
        self.tol = tol
        self.max_iter = max_iter

    def fit(self, X, y, *, sensitive_features=None):
        """
        Fit the fair representation learner to the provided data.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            The input samples.

        y : array-like of shape (n_samples,)
            The target values.

        sensitive_features : array-like or None, default=None
            Sensitive features to be considered whose groups will be used to enforce statistical
            parity. If None, the model will fall back to a Logistic Regression classifier.

        Returns
        -------
        self : FairRepresentationLearner
            Returns the fitted instance.
        """
        X, y = self._validate_X_y(X, y)

        _, _, sensitive_features, _ = _validate_and_reformat_input(
            X,
            y,
            sensitive_features=sensitive_features,
            expect_y=True,
            expect_sensitive_features=False,
            enforce_binary_labels=True,
        )
        assert sensitive_features is None or isinstance(sensitive_features, pd.Series)

        self.n_features_in_ = X.shape[1]
        random_state = check_random_state(self.random_state)

        if sensitive_features is None:
            LOGGER.warning("No sensitive features provided. Fitting a Logistic Regression.")

            return self._optimize_without_sensitive_features(X, y, random_state)

        return self._optimize_with_sensitive_features(X, y, sensitive_features, random_state)

    def _optimize_with_sensitive_features(
        self, X, y, sensitive_features: pd.Series, random_state: np.random.RandomState
    ):
        """
        Minimize the loss given the sensitive features.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            The input samples.
        y : array-like of shape (n_samples,)
            The target values.
        sensitive_features : pd.Series
            The sensitive features for each sample.
        random_state : np.random.RandomState
            The random state for reproducibility. Used for initializing the optimization.

        Returns
        -------
        self : FairRepresentationLearner
            Returns self.

        Raises
        ------
        RuntimeError
            If the loss minimization fails.
        """
        self._groups = sensitive_features.unique()

        # Dimension of each v_k prototype vector
        self._prototype_dim = X.shape[1]
        # Dimension of M, the latent representation stochastic mapping from X to Z
        self._latent_mapping_size = len(self._groups) * self.n_prototypes
        # Dimension of the prototype predictions vector w
        self._prototype_predictions_size = self.n_prototypes
        # Total size of the prototype vectors
        self._prototype_vectors_size = self.n_prototypes * self._prototype_dim

        self._optimizer_size = (
            self.n_prototypes * self._prototype_dim  # V
            + self._prototype_predictions_size  # w
            + self._prototype_dim  # alpha: the weight of each dimension in the distance computation
        )

        def objective(x: np.ndarray, X, y) -> float:
            assert x.shape == (self._optimizer_size,)
            # Compute the reconstruction error
            V = x[: self._prototype_vectors_size].reshape((self.n_prototypes, self._prototype_dim))
            alpha = x[-self._prototype_dim :]
            M = self._get_latent_mapping(X, V, dimension_weights=alpha)
            X_hat = M @ V
            reconstruction_error = np.mean(np.sum((X - X_hat) ** 2, axis=1))

            # Compute the fairness error
            # Compute the mean prototype probabilities for each group
            M_gk = np.array(
                [np.mean(M[sensitive_features == group], axis=0) for group in self._groups]
            )
            # Compute the mean difference between mean prototype probabilities for each group
            group_combinations = np.triu_indices(n=len(self._groups), k=1)
            fairness_error = np.mean(
                np.abs(M_gk[group_combinations[0], None] - M_gk[group_combinations[1], None])
            )

            # Compute the classification error
            w = x[self._prototype_vectors_size : -self._prototype_dim]
            y_hat = M @ w
            classification_error = log_loss(y, y_hat)

            return (
                self.Ax * reconstruction_error
                + self.Ay * classification_error
                + self.Az * fairness_error
            )

        # Initialize the prototype vectors v_k
        V0 = random_state.rand(self.n_prototypes, self._prototype_dim)

        # Initialize the prototype predictions w_k
        w0 = random_state.rand(self.n_prototypes)

        # Initialize the dimension weights alpha
        alpha0 = np.ones(self._prototype_dim)

        x0 = np.concatenate([V0.flatten(), w0, alpha0])

        bounds = (
            [(None, None)] * self._prototype_vectors_size  # The prototype vectors are unbounded
            + [(0, 1)]
            * self._prototype_predictions_size  # The prototype predictions are in [0, 1]
            + [(0, None)] * self._prototype_dim  # The dimension weights are non-negative
        )

        try:
            result: OptimizeResult = minimize(
                objective,
                x0=x0,
                bounds=bounds,
                args=(X, y),
                method="L-BFGS-B",
                tol=self.tol,
                options={"maxiter": self.max_iter},
            )
        except Exception as optimization_error:
            raise RuntimeError("The loss minimization failed.") from optimization_error

        self.coef_ = result.x[self._prototype_vectors_size : -self._prototype_dim]
        self._prototypes_ = result.x[: self._prototype_vectors_size].reshape(
            (self.n_prototypes, self._prototype_dim)
        )
        self._alpha_ = result.x[-self._prototype_dim :]
        self.n_iter_ = result.nit

        self._fall_back_classifier = None

        return self

    def _optimize_without_sensitive_features(self, X, y, random_state: np.random.RandomState):
        """
        Optimize the model without considering sensitive features.

        This method trains a logistic regression model on the provided features and labels,
        and stores the resulting coefficients and other relevant attributes.

        Parameters
        ----------
        X : array-like or sparse matrix of shape (n_samples, n_features)
            The input samples.

        y : array-like of shape (n_samples,)
            The target values.

        random_state : np.random.RandomState
            The random state to use for reproducibility.

        Returns
        -------
        self : FairRepresentationLearner
            Returns the instance itself.
        """
        self._groups = pd.Series()

        fallback_classifier = LogisticRegression(
            tol=self.tol, max_iter=self.max_iter, random_state=random_state
        )

        self._fall_back_classifier = fallback_classifier.fit(X, y)

        self.coef_ = self._fall_back_classifier.coef_
        self._prototypes_ = None
        self._alpha_ = None
        self.n_iter_ = self._fall_back_classifier.n_iter_

        return self

    def transform(self, X) -> np.ndarray:
        """
        Transform the input data X using the learned fair representation.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            The input data to transform.

        Returns
        -------
        np.ndarray
            The transformed data.

        Notes
        -----
        This method checks if the model is fitted, validates the input data,
        and then applies the learned fair representation transformation.
        If a fallback classifier is set, it returns the input data as is.
        Otherwise, it computes the latent mapping and returns the transformed data.
        """
        check_is_fitted(self)

        X = validate_data(self, X, reset=False)

        if self._fall_back_classifier is not None:
            return X

        M = self._get_latent_mapping(X, self._prototypes_, dimension_weights=self._alpha_)
        return M @ self._prototypes_

    def predict_proba(self, X) -> np.ndarray:
        """
        Predict class probabilities for the input samples X.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            The input samples.

        Returns
        -------
        np.ndarray of shape (n_samples, 2)
            The class probabilities of the input samples. The first column
            represents the probability of the negative class, and the second
            column represents the probability of the positive class.

        Raises
        ------
        NotFittedError
            If the estimator is not fitted yet.
        """
        check_is_fitted(self)

        X = validate_data(self, X, reset=False)

        if self._fall_back_classifier is not None:
            return self._fall_back_classifier.predict_proba(X)

        M = self._get_latent_mapping(X, self._prototypes_, dimension_weights=self.alpha_)
        positive_proba = M @ self.coef_
        return np.c_[1 - positive_proba, positive_proba]

    def predict(self, X) -> np.ndarray:
        """
        Predict the labels for the given input data.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            The input data to predict.

        Returns
        -------
        np.ndarray
            The predicted labels for the input data.
        """
        check_is_fitted(self)

        X = validate_data(self, X, reset=False)

        binary_predictions = self.predict_proba(X)[:, 1] > 0.5

        return self._label_encoder.inverse_transform(binary_predictions)

    @property
    def prototypes_(self) -> np.ndarray:
        check_is_fitted(self)

        if self._prototypes_ is None:
            raise AttributeError(
                "No sensitive features provided when fitting. No prototypes were learned."
            )

        return self._prototypes_

    @property
    def alpha_(self) -> np.ndarray:
        check_is_fitted(self)

        if self._alpha_ is None:
            raise AttributeError(
                "No sensitive features provided when fitting. No distance was learned."
            )

        return self._alpha_

    @staticmethod
    def _get_latent_mapping(
        X, prototypes: np.ndarray, dimension_weights: np.ndarray
    ) -> np.ndarray:
        """
        Compute the latent mapping of the input data X to the given prototypes.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            The input data to be mapped.
        prototypes : np.ndarray of shape (n_prototypes, n_features)
            The prototype vectors to which the input data will be mapped.
        dimension_weights : np.ndarray of shape (n_features,)
            The weights for each dimension used in the distance calculation.

        Returns
        -------
        np.ndarray of shape (n_samples, n_prototypes)
            The latent mapping of the input data to the prototypes, where each
            element represents the softmax-transformed negative distance between
            a sample and a prototype.
        """
        distances = cdist(X, prototypes, metric="euclidean", w=dimension_weights)
        M = softmax(-distances, axis=1)
        return M

    def _validate_X_y(self, X, y) -> tuple[np.ndarray, np.ndarray]:
        """
        Validate and preprocess the input features and target labels.

        Parameters
        ----------
        X : array-like
            The input features.
        y : array-like
            The target labels.

        Returns
        -------
        tuple of np.ndarray
            The validated and preprocessed input features and target labels.

        Raises
        ------
        ValueError
            If the target labels are not binary.
        """
        X, y = validate_data(self, X, y=y, allow_nd=True, ensure_2d=False, ensure_all_finite=True)

        y_type = type_of_target(y, input_name="y", raise_unknown=True)
        if y_type != "binary":
            raise ValueError(
                f"Unknown label type: {y_type}. Only binary classification is supported."
            )
        self.classes_ = np.unique(y)
        self._label_encoder = LabelEncoder().fit(y)
        y = self._label_encoder.transform(y)

        return X, y

    def __sklearn_tags__(self):
        tags = super().__sklearn_tags__()
        tags.classifier_tags.multi_class = False
        return tags