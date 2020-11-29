import sys, os
import numpy as np
import torch
import copy
import torch.nn as nn

import omnifig as fig

from .. import util
import torch.multiprocessing as mp
from itertools import chain

class Model(nn.Module):  # any vector function
	def __init__(self, din=None, dout=None):
		super().__init__()
		self.din = din
		self.dout = dout
		self.device = 'cpu'
		
		self.savable_buffers = set()
		
		self.volatile = util.TensorDict()
		self._saveable_attrs = set()

	def register_buffer(self, name, tensor=None, save=True):
		super().register_buffer(name, tensor)
		self.savable_buffers.add(name)

	def register_attr(self, name, data=None):
		self._saveable_attrs.add(name)
		self.__setattr__(name, data)

	def cuda(self, device=None):
		self.device = 'cuda' if device is None else device
		self.volatile.to(self.device)
		super(Model, self).cuda(device)

	def cpu(self):
		self.device = 'cpu'
		self.volatile.to('cpu')
		super(Model, self).cpu()

	def to(self, device):
		self.device = device
		self.volatile.to(self.device)
		super(Model, self).to(device)

	def state_dict(self, *args, **kwargs):
		volatile = self.volatile
		self.volatile = None
		
		out = super().state_dict(*args, **kwargs)
		
		self.volatile = volatile
		return {'parameters': out,
		        'buffers': {name: getattr(self, name, None)
		                    for name in self.savable_buffers},
		        'attrs': {name: getattr(self, name, None)
		                  for name in self._saveable_attrs}}
	
	def load_state_dict(self, state_dict, **kwargs):
		for name, buffer in state_dict.get('buffers', {}).items():
			self.register_buffer(name, buffer, save=True)
		for name, data in state_dict.get('attrs', {}).items():
			self.register_attr(name, data)
		return super().load_state_dict(state_dict['parameters']
		                               if 'parameters' in state_dict
		                               else state_dict, **kwargs)

	def pre_epoch(self, mode, records): # called at the beginning of each epoch
		pass

	def post_epoch(self, mode, records, stats=None): # called at the end of each epoch
		pass

	def get_hparams(self):
		return {}

class CompositeModel(Model): # TODO integrate with component based models
	def __init__(self, *models):
		super().__init__(models[0].din, models[-1].dout)
		self.models = nn.ModuleList(models)

	def forward(self, x):
		for m in self.models:
			x = m(x)
		return x

class ModedModel(util.Mode, Model):
	def switch_mode(self, mode):
		super().switch_mode(mode)
		if mode == 'train':
			self.train()
		else:
			self.eval()

@fig.AutoModifier('generative')
class Generative(object):
	def sample_prior(self, N=1):
		raise NotImplementedError

	def generate(self, N=1, q=None):
		if q is None:
			q = self.sample_prior(N)
		return self(q)

@fig.AutoModifier('encodable')
class Encodable(object):
	def encode(self, x): # by default this is just forward pass
		return self(x)

@fig.AutoModifier('decodable')
class Decodable(object): # by default this is just the forward pass
	def decode(self, q):
		return self(q)

@fig.AutoModifier('invertible')
class Invertible(object):
	def inverse(self, *args, **kwargs):
		raise NotImplementedError

class Savable(Model):
	
	def save_checkpoint(self, *paths, **data):
		data.update({
			'model_str': str(self),
			'model_state': self.state_dict(),
		})
		
		self.save_data(*paths, data=data)
		
	def save_data(self, *paths, data={}):
		for path in paths:
			torch.save(data, path)

class Recordable(util.StatsContainer, Model):
	pass

class Maintained(Model):

	def maintenance(self, tick, info=None):
		pass

	

class Visualizable(Recordable):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.reset_viz_counter()
	def reset_viz_counter(self):
		self._viz_counter = 0
	def visualize(self, info, logger): # records output directly to logger
		with torch.no_grad():
			self._visualize(info, logger)
		self._viz_counter += 1
			
	def _visualize(self, info, logger):
		pass # by default nothing is visualized
		# raise NotImplementedError

	# def pre_epoch(self, mode, epoch):
	# 	self.reset_viz_counter()
	# 	super().pre_epoch(mode, epoch)

class Evaluatable(Recordable): # TODO: maybe not needed

	def evaluate(self, loader, logger=None, A=None, run=None):
		# self._eval_counter += 1
		return self._evaluate(loader, logger=logger, A=A, run=run)

	def _evaluate(self, loader, logger=None, A=None, run=None):
		pass # by default eval does nothing
	# 	raise NotImplementedError



@fig.AutoModifier('optim')
class Optimizable(Recordable):

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.optim = None

	def set_optim(self, A=None):
		
		if self.optim is not None:
			return
		
		sub_optims = {}
		for name, child in self.named_children():
			if isinstance(child, Optimizable) and child.optim is not None:
				sub_optims[name] = child.optim

		if len(sub_optims):

			params = [] # param eters not already covered by sub_optims
			for name, param in self.named_parameters():
				name = name.split('.')[0]
				if name not in sub_optims:
					params.append(param)
		
			if len(sub_optims) == 1 and len(params) == 0: # everything convered by a single sub_optim
				optim = next(iter(sub_optims.values()))
			else:
				if len(params):
					if 'me' in sub_optims:
						raise Exception('invalid names: {} already in {}'.format('me', sub_optims.keys()))
					# sub_optims['me'] = util.default_create_optim(params, optim_info)
					sub_optims['me'] = A.pull('optim')
					sub_optims['me'].prep(params)
				optim = util.Complex_Optimizer(**sub_optims)
		else:
			optim = A.pull('optim')
			optim.prep(self.parameters())
			# optim = util.default_create_optim(self.parameters(), optim_info)
			
		self.optim = optim
		
		return optim

	def _optim_step(self, loss): # should only be called during training
		if self.optim is None:
			raise Exception('Optimizer not set')
		self.optim.zero_grad()
		loss.backward()
		self.optim.step()

	def load_state_dict(self, state_dict, strict=True):
		if self.optim is not None and 'optim' in state_dict:
			self.optim.load_state_dict(state_dict['optim'])
		super().load_state_dict(state_dict['model'], strict=strict)

	def state_dict(self, *args, **kwargs):
		state_dict = {
			'model': super().state_dict(*args, **kwargs),
		}
		if self.optim is not None:
			state_dict['optim'] = self.optim.state_dict()
		return state_dict


class Regularizable(object):
	def regularize(self, q):
		return torch.tensor(0).type_as(q)

class Cacheable(Model):
	def __init__(self, *args, cache_device=None, **kwargs):
		self._cache_names = set()
		self._cache_device = cache_device
		super().__init__(*args, **kwargs)

	def register_cache(self, name, value=None):
		self._cache_names.add(name)

		setattr(self, name,
		        value if self._cache_device is None else value.to(self._cache_device))

	def clear_cache(self):
		for name in self._cache_names:
			setattr(self, name, None)

	def cuda(self, device=None):
		super().cuda(device)
		if self._cache_device is None:
			for name in self._cache_names:
				obj = getattr(self, name)
				if obj is not None:
					setattr(self, name, obj.cuda(device))

	def cpu(self):
		super().cpu()
		if self._cache_device is None:
			for name in self._cache_names:
				obj = getattr(self, name)
				if obj is not None:
					setattr(self, name, obj.cpu())

	def to(self, device):
		super().to(device)
		if self._cache_device is None:
			for name in self._cache_names:
				obj = getattr(self, name)
				if obj is not None:
					setattr(self, name, obj.to(device))

	def state_dict(self, *args, **kwargs): # dont include cached items in the state_dict
		cache = {}
		for name in self._cache_names:
			cache[name] = getattr(self, name)
			delattr(self, name)

		out = super().state_dict(*args, **kwargs)

		for name, value in cache.items():
			setattr(self, name, value)

		return out


class Trainable_Model(Optimizable, Savable, Model): # top level - must be implemented to train
	def step(self, batch):  # Override pre-processing mixins
		return self._step(batch)

	def test(self, batch):  # Override pre-processing mixins
		return self._test(batch)

	def _step(self, batch, out=None):  # Override post-processing mixins
		if out is None:
			out = util.TensorDict()
		return out

	def _test(self, batch):  # Override post-processing mixins
		return self._step(batch)  # by default do the same thing as during training

	def prep(self, *datasets):
		pass

	# NOTE: never call an optimizer outside of _step (not in mixinable functions)
	# NOTE: before any call to an optimizer check with self.train_me()
	def train_me(self):
		return self.training and self.optim is not None



class Full_Model(Cacheable, Visualizable, Evaluatable, Trainable_Model): # simple shortcut for subclassing
	pass

