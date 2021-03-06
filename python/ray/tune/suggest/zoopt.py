import copy
import logging
import ray.cloudpickle as pickle

try:
    import zoopt
except ImportError:
    zoopt = None

from ray.tune.suggest.suggestion import SuggestionAlgorithm

logger = logging.getLogger(__name__)


class ZOOptSearch(SuggestionAlgorithm):
    """A wrapper around ZOOpt to provide trial suggestions.

    Requires zoopt package (>=0.4.0) to be installed. You can install it
    with the command: ``pip install -U zoopt``.

    Parameters:
        algo (str): To specify an algorithm in zoopt you want to use.
            Only support ASRacos currently.
        budget (int): Number of samples.
        dim_dict (dict): Dimension dictionary.
            For continuous dimensions: (continuous, search_range, precision);
            For discrete dimensions: (discrete, search_range, has_order).
            More details can be found in zoopt package.
        max_concurrent (int): Number of maximum concurrent trials.
            Defaults to 10.
        metric (str): The training result objective value attribute.
            Defaults to "episode_reward_mean".
        mode (str): One of {min, max}. Determines whether objective is
            minimizing or maximizing the metric attribute.
            Defaults to "min".

    .. code-block:: python

        from ray.tune import run
        from ray.tune.suggest.zoopt import ZOOptSearch
        from zoopt import ValueType

        dim_dict = {
            "height": (ValueType.CONTINUOUS, [-10, 10], 1e-2),
            "width": (ValueType.DISCRETE, [-10, 10], False)
        }

        config = {
            "num_samples": 200,
            "config": {
                "iterations": 10,  # evaluation times
            },
            "stop": {
                "timesteps_total": 10  # cumstom stop rules
            }
        }

        zoopt_search = ZOOptSearch(
            algo="Asracos",  # only support Asracos currently
            budget=config["num_samples"],
            dim_dict=dim_dict,
            max_concurrent=4,
            metric="mean_loss",
            mode="min")

        run(my_objective,
            search_alg=zoopt_search,
            name="zoopt_search",
            **config)

    """

    optimizer = None

    def __init__(self,
                 algo="asracos",
                 budget=None,
                 dim_dict=None,
                 max_concurrent=10,
                 metric="episode_reward_mean",
                 mode="min",
                 **kwargs):
        assert zoopt is not None, "Zoopt not found - please install zoopt."
        assert budget is not None, "`budget` should not be None!"
        assert dim_dict is not None, "`dim_list` should not be None!"
        assert type(max_concurrent) is int and max_concurrent > 0
        assert mode in ["min", "max"], "`mode` must be 'min' or 'max'!"
        _algo = algo.lower()
        assert _algo in ["asracos", "sracos"
                         ], "`algo` must be in ['asracos', 'sracos'] currently"

        self._max_concurrent = max_concurrent
        self._metric = metric
        if mode == "max":
            self._metric_op = -1.
        elif mode == "min":
            self._metric_op = 1.
        self._live_trial_mapping = {}

        self._dim_keys = []
        _dim_list = []
        for k in dim_dict:
            self._dim_keys.append(k)
            _dim_list.append(dim_dict[k])

        dim = zoopt.Dimension2(_dim_list)
        par = zoopt.Parameter(budget=budget)
        if _algo == "sracos" or _algo == "asracos":
            from zoopt.algos.opt_algorithms.racos.sracos import SRacosTune
            self.optimizer = SRacosTune(dimension=dim, parameter=par)

        self.solution_dict = {}
        self.best_solution_list = []

        super(ZOOptSearch, self).__init__(
            metric=self._metric, mode=mode, **kwargs)

    def suggest(self, trial_id):
        if self._num_live_trials() >= self._max_concurrent:
            return None

        _solution = self.optimizer.suggest()
        if _solution:
            self.solution_dict[str(trial_id)] = _solution
            _x = _solution.get_x()
            new_trial = dict(zip(self._dim_keys, _x))
            self._live_trial_mapping[trial_id] = new_trial
            return copy.deepcopy(new_trial)

    def on_trial_result(self, trial_id, result):
        pass

    def on_trial_complete(self,
                          trial_id,
                          result=None,
                          error=False,
                          early_terminated=False):
        """Notification for the completion of trial."""
        if result:
            _solution = self.solution_dict[str(trial_id)]
            _best_solution_so_far = self.optimizer.complete(
                _solution, self._metric_op * result[self._metric])
            if _best_solution_so_far:
                self.best_solution_list.append(_best_solution_so_far)
            self._process_result(trial_id, result, early_terminated)

        del self._live_trial_mapping[trial_id]

    def _process_result(self, trial_id, result, early_terminated=False):
        if early_terminated and self._use_early_stopped is False:
            return

    def _num_live_trials(self):
        return len(self._live_trial_mapping)

    def save(self, checkpoint_dir):
        trials_object = self.optimizer
        with open(checkpoint_dir, "wb") as output:
            pickle.dump(trials_object, output)

    def restore(self, checkpoint_dir):
        with open(checkpoint_dir, "rb") as input:
            trials_object = pickle.load(input)
        self.optimizer = trials_object
