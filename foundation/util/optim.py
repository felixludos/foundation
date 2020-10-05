
from itertools import chain
import torch
from torch import optim as O
from torch import nn
from torch.optim import Optimizer
try:
	from ranger import Ranger
except ImportError:
	print('WARNING: failed to import Ranger optimizer')
from .stats import StatsMeter

def get_optimizer(optim_type, parameters, lr=1e-3, weight_decay=0, momentum=0, beta1=.9, beta2=.999, **optim_args):
	if optim_type == 'sgd':
		optimizer = O.SGD(parameters,lr=lr, weight_decay=weight_decay, momentum=momentum, **optim_args)
	elif optim_type == 'rmsprop':
		optimizer = O.RMSprop(parameters, lr=lr, weight_decay=weight_decay, momentum=momentum, **optim_args)
	elif optim_type == 'adam':
		optimizer = O.Adam(parameters, lr=lr, weight_decay=weight_decay, betas=(beta1, beta2), **optim_args)
	elif optim_type == 'adamw':
		optimizer = O.AdamW(parameters, lr=lr, weight_decay=weight_decay, betas=(beta1, beta2), **optim_args)
	elif optim_type == 'ranger':
		optimizer = Ranger(parameters, lr=lr, weight_decay=weight_decay,betas=(beta1, beta2), **optim_args) #alpha=0.5, k=6, N_sma_threshhold=5, betas=(.95,0.999))
	elif optim_type == 'cg':
		optimizer = Conjugate_Gradient(parameters, **optim_args)
	elif optim_type == 'rprop':
		optimizer = O.Rprop(parameters, lr=lr)
	elif optim_type == 'adagrad':
		optimizer = O.Adagrad(parameters, lr=lr, weight_decay=weight_decay)
	elif optim_type == 'adadelta':
		optimizer = O.Adadelta(parameters, lr=lr, weight_decay=weight_decay)
	else:
		assert False, "Unknown optimizer type: " + optim_type
	return optimizer

def default_create_optim(parameters, info): # info is a train.Config

	kwargs = {

		'optim_type': info.pull('optim_type'),

		'parameters': parameters,

		'lr': info.pull('lr', 1e-3),
		'weight_decay': info.pull('weight_decay', 0),
		'momentum': info.pull('momentum', 0),

		'beta1': info.pull('beta1', .9),
		'beta2': info.pull('beta2', .999),
	}

	return get_optimizer(**kwargs)

def default_create_scheduler(optimizer, info):

	if 'scheduler_type' not in info:
		return None

	name = info.pull('scheduler_type')

	freq = info.pull('scheduler_freq', None)
	# if freq is None:
	# 	print('Scheduler will step at the end of each epoch')
	# else:
	# 	print(f'Scheduler will step every {freq} iterations')
	cut_after = info.pull('scheduler_cut_after', None)

	min_lr = info.pull('scheduler_min_lr', 0.)
	last = info.pull('scheduler_last_epoch', -1)

	common = {
		'cut_after': cut_after,
		'freq': freq,
	}

	if name == 'step':

		factor = info.pull('scheduler_decay', 0.1)

		step_size = info.pull('scheduler_step')
		out = StepLR(optimizer, step_size=step_size, gamma=factor, last_epoch=last,
		             req_loss=False, **common)
	elif name == 'plateau':

		factor = info.pull('scheduler_decay', 0.1)
		patience = info.pull('scheduler_patience', 10)
		cooldown = info.pull('scheduler_cooldown', 0)

		out = ReduceOnPlateau(optimizer, factor=factor, patience=patience, verbose=True,
		                                       min_lr=min_lr, cooldown=cooldown,
		                      req_loss=True, **common)


	elif name == 'cos':
		num_steps = info.pull('scheduler_total_steps', None) # num_steps
		if num_steps is None:
			if freq is None or freq <= 0:
				raise Exception('cos scheduler needs to know the max number of steps')
			num_steps = info.pull('scheduler_total_iterations', '<>training.step_limit') // freq \
				- info.pull('scheduler_early_stop', 0)

		eta_min = min_lr
		
		out = CosineAnnealing(optimizer, T_max=num_steps, eta_min=eta_min, last_epoch=last,
		                      req_loss=False, **common)

	else:
		raise Exception(f'unknown name {name}')

	return out

class Base_Scheduler(O.lr_scheduler._LRScheduler):

	def __init__(self, *args, cut_after=None, freq=None, req_loss=None, **kwargs):
		if freq <= 0:
			freq = None
		self.freq = freq
		self.cut_after = cut_after
		self.req_loss = req_loss
		super().__init__(*args, **kwargs)

	def requires_loss(self):
		return self.req_loss

	def epoch_end(self, *args, **kwargs):
		if self.freq is None:
			self.step(*args, **kwargs)

	def maintain(self, step, *args, **kwargs):
		if self.freq is not None and step % self.freq == 0:
			self.step(*args, **kwargs)

	def step(self, *args, **kwargs):
		if self.cut_after is not None and self._step_count > self.cut_after:
			return
		return super().step(*args, **kwargs)

class StepLR(Base_Scheduler, O.lr_scheduler.StepLR):
	def __str__(self):
		return self.__repr__()

	def __repr__(self):
		return 'StepLR(step={}, gamma={})'.format(self.step_size, self.gamma)


class MultiStepLR(Base_Scheduler, O.lr_scheduler.MultiStepLR):
	def __str__(self):
		return self.__repr__()

	def __repr__(self):
		return 'MultiStepLR(milestones={}, gamma={})'.format(self.milestones, self.gamma)

class ReduceOnPlateau(Base_Scheduler, O.lr_scheduler.ReduceLROnPlateau):
	def __str__(self):
		return self.__repr__()

	def __repr__(self):
		return 'ReduceOnPlateau(factor={}, patience={}, cooldown={})'.format(self.factor, self.patience, self.cooldown)

class CosineAnnealing(Base_Scheduler, O.lr_scheduler.CosineAnnealingLR):
	def __str__(self):
		return self.__repr__()
	
	def __repr__(self):
		return 'CosineAnnealing(T_max={},eta_min={})'.format(self.T_max, self.eta_min)


class Complex_Optimizer(Optimizer):
	def __init__(self, **optims):
		self.__dict__['optims'] = None
		super().__init__([{}], None)
		self.__dict__['optims'] = optims
		self.group_params()
		# self._update_groups()
		# self._update_named_groups()
		# self.__dict__['param_groups'] = [grp for grp in chain(*[o.param_groups for o in optims.values()])]

	def group_params(self):
		self.param_groups = sum([[p for p in o.param_groups] for o in self.optims.values()], [])

	def _update_groups(self):
		self.param_groups = self._param_group_gen()

	def _param_group_gen(self):
		for optim in self.optims.values():
			for grp in optim.param_groups:
				yield grp
		self._update_groups()

	def _update_named_groups(self):
		self.named_param_groups = self._named_param_group_gen()

	def _named_param_group_gen(self):
		for key, optim in self.optims.items():
			for grp in optim.param_groups:
				yield (key, grp)
		self._update_named_groups()

	def add_param_group(self, *args, **kwargs): # probably shouldnt be used
		return
		# raise Exception('invalid for complex optimizers')

	def load_state_dict(self, state_dict):
		for name, optim in self.optims.items():
			optim.load_state_dict(state_dict[name])
		self.group_params() # important to get loaded param groups

	def state_dict(self):
		return {name:optim.state_dict() for name, optim in self.optims.items()}

	def zero_grad(self):
		for optim in self.optims.values():
			optim.zero_grad()

	def step(self, closure=None): # can and perhaps should be overridden
		for optim in self.optims.values():
			optim.step(closure)

	def __iter__(self):
		return iter(self.optims)
	def items(self):
		return self.optims.items()

	def __len__(self):
		return len(self.optims)

	def __getitem__(self, item):
		return self.optims[item]
	def __setitem__(self, key, value):
		raise NotImplementedError
		self.optims[key] = value
	def __delitem__(self, key):
		del self.optims[key]

	def __getattr__(self, item):
		try:
			return super().__getattribute__(item)
		except AttributeError:
			return self.optims[item]
	def __setattr__(self, key, value):
		# raise NotImplementedError
		if isinstance(value, Optimizer):
			raise NotImplementedError
			self.__setitem__(key, value)
		else:
			super().__setattr__(key, value)
	def __delattr__(self, item):
		try:
			super().__delattr__(item)
		except AttributeError:
			self.__delitem__(item)

	def __str__(self):
		s = ['Complex-Optimizer(']

		for k,v in self.optims.items():
			s.append('  {}: {}'.format(k,str(v).replace('\n', '\n    ')))

		s.append(')')

		return '\n'.join(s)


class Complex_Scheduler(object):
	def __init__(self, **subs):
		self.sub = subs
		self.req_loss = bool(sum([sch.req_loss for sch in self.sub.values()]))

	def load_state_dict(self, state_dict):
		for name, sch in self.sub.items():
			sch.load_state_dict(state_dict[name])

	def state_dict(self):
		return {name:sch.state_dict() for name, sch in self.sub.items()}

	def requires_loss(self):
		for sch in self.sub.values():
			if sch.requires_loss():
				return True
		return False

	def maintain(self, step, *args, **kwargs):
		for sch in self.sub.values():
			sch.maintain(step, *args, **kwargs)

	def epoch_end(self, *args, **kwargs):
		for sch in self.sub.values():
			sch.epoch_end(*args, **kwargs)

	def step(self, val=None): # can and perhaps should be overridden
		for sch in self.sub.values():
			if sch.req_loss:
				sch.step(val)
			else:
				sch.step()

	def __str__(self):
		s = ['Complex-Scheduler(']

		for k,v in self.sub.items():
			s.append('  {}: {}'.format(k,str(v).replace('\n', '\n    ')))

		s.append(')')

		return '\n'.join(s)


class Conjugate_Gradient(Optimizer):
	def __init__(self, params, step_size, nsteps, residual_tol=1e-10, ret_stats=False):
		super().__init__(params, {'step-size': step_size,
                                  'nsteps': nsteps,
                                  'res-tol': residual_tol,
                                  'ret_stats': ret_stats})

		self.stats = [StatsMeter('v-norm', 'n-norm', 'alpha') for _ in self.param_groups]

	def cg_solve(self, apply_A, b, x0=None, res_tol=1e-10, nsteps=10):

		if x0 is None:
			x = torch.zeros(*b.size())
			r = b.clone()
		else:
			x = x0
			r = b - apply_A(x)

		p = r.clone()
		rdotr = r @ r
		for i in range(nsteps):
			Ap = apply_A(p)
			alpha = rdotr / (p @ Ap)
			x += alpha * p
			r -= alpha * Ap
			new_rdotr = r @ r
			beta = new_rdotr / rdotr
			p = r + beta * p
			rdotr = new_rdotr
			if rdotr < res_tol:
				break

		return x

	def step(self, apply_A):

		for group, stats in zip(self.param_groups, self.stats):

			# get v (from backward pass)
			p = nn.parameters_to_vector(group)
			v = p.grad

			# compute n with cg
			n = self.cg_solve(apply_A, v, res_tol=group['res-tol'], nsteps=group['nsteps'])

			# compute learning rate
			alpha = (2 * group['step-size'] / (v @ n)).sqrt()

			p.data.add( n.mul_(alpha) )
			nn.vector_to_parameters(p, group)

			if group['ret_stats']:
				stats.update('alpha', alpha)
				stats.update('v-norm', v.norm())
				stats.update('n-norm', n.norm())