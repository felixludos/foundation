
import sys, os, time
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision
from .. import util
from torch.utils.data import TensorDataset, DataLoader, ConcatDataset
from ..data.loaders import BatchedDataLoader
from ..data.collectors import *
from ..data.collate import _collate_movable


FD_PATH = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DEFAULT_DATA_PATH = os.path.join(os.path.dirname(FD_PATH),'local_data')
# print(FD_PATH)


def get_loaders(datasets, batch_size=None, num_workers=0, shuffle=True, pin_memory=True,
		   drop_last=False, worker_init_fn=None, silent=False, allow_batched=True):

	if shuffle == 'all':
		shuffles = [True]*3
	elif shuffle:
		shuffles = [True, False, False]
	else:
		shuffles = [False]*3

	if batch_size is None:
		batch_size = 64 # TODO: maybe choose batch size smartly

	ds = datasets[0]

	loader_cls = DataLoader
	kwargs = {
		'batch_size': batch_size,
		'drop_last': drop_last,
	}

	if allow_batched:
		try:
			assert ds.allow_batched()
		except (AttributeError, AssertionError):
			pass
		else:
			print('Using batched data loader')
			loader_cls = BatchedDataLoader
	else:

		try:
			assert ds.get_device() == 'cpu'
		except AttributeError:
			pass
		except AssertionError:
			pin_memory = False

		kwargs.update({
			'pin_memory': pin_memory,
			'worker_init_fn': worker_init_fn,
			'num_workers': num_workers,
		})


	loaders = [loader_cls(ds, shuffle=s, **kwargs) for ds, s in zip(datasets, shuffles)]

	if not silent:
		trainloader = loaders[0]
		testloader = None if len(loaders) < 2 else loaders[-1]
		valloader = None if len(loaders) < 3 else loaders[1]

		print('traindata len={}, trainloader len={}'.format(len(datasets[0]), len(trainloader)))
		if valloader is not None:
			print('valdata len={}, valloader len={}'.format(len(datasets[1]), len(valloader)))
		if testloader is not None:
			print('testdata len={}, testloader len={}'.format(len(datasets[-1]), len(testloader)))
		print('Batch size: {} samples'.format(batch_size))

	# if len(loaders) == 1:
	# 	return loaders[0]
	return loaders


def simple_split_dataset(dataset, split, shuffle=True):
	'''

	:param dataset:
	:param split: split percent as ratio [0,1]
	:param shuffle:
	:return:
	'''

	assert 0 < split < 1

	if shuffle:
		dataset = Shuffle_Dataset(dataset)

	ncut = int(len(dataset) * split)

	part1 = Subset_Dataset(dataset, torch.arange(0,ncut))
	part2 = Subset_Dataset(dataset, torch.arange(ncut, len(dataset)))

	return part1, part2

def split_dataset(dataset, split1, split2=None, shuffle=True):
	p1, p2 = simple_split_dataset(dataset, split1, shuffle=shuffle)
	if split2 is None:
		return p1, p2
	split2 = split2 / (1 - split1)
	p2, p3 = simple_split_dataset(p2, split2, shuffle=False)
	return p1, p2, p3

_dataset_registry = {}
_testable_registry = set()

def register_dataset(name, cls, *args, **kwargs):
	_dataset_registry[name] = cls, args, kwargs
	if issubclass(cls, Testable_Dataset):
		_testable_registry.add(name)

def get_dataset(name, *new_args, **new_kwargs):

	assert name in _dataset_registry, 'Dataset {} not found (have you registered it?)'.format(name)

	cls, args, kwargs = _dataset_registry[name]

	if len(new_args): # get overwritten
		args = new_args
	kwargs.update(new_kwargs)

	dataset = cls(*args, **kwargs)

	return dataset

class UntestableDatasetError(Exception):
	def __init__(self, name):
		super().__init__('Unable to only load testset for {}, since it doesnt subclass Testable_Dataset'.format(name))

def default_load_data(A, mode='train'):
	'''
	req: A.dataset, A.device
	optional: A.dataset[mode], A.dataset.device
	adds: A.data, A.data.din, A.data.dout
	:param A:
	:return:
	'''

	info = A.dataset
	if mode in A.dataset:
		info = A.dataset[mode] # TODO: merge A.dataset[mode] with defaults in A.dataset

	name = info.name
	args = info.args if 'args' in info else ()
	kwargs = info.kwargs if 'kwargs' in info else {}

	if mode == 'test':
		if name not in _testable_registry:
			raise UntestableDatasetError(name)
		kwargs['train'] = False

	dataset = get_dataset(info.name, *args, **kwargs)

	device = A.device if 'device' not in info else info.device
	try:
		dataset.to(device)
		print('Dataset {} moved to {}'.format(device))
	except AttributeError:
		pass
	except RuntimeError:
		print('Not enough memory to move dataset to {}'.format(device))

	try:
		din, dout = dataset.get_info()
		A.data.din, A.data.dout = din, dout
		print('Dataset din={}, dout={}'.format(din, dout))
	except AttributeError:
		pass

	if mode == 'test':
		return dataset

	trainsets = dataset,
	testset = None
	if 'test_split' in info:
		assert 0 < info.test_split < 1, 'cant split: {}'.format(info.val_split)
		*trainsets, testset = simple_split_dataset(dataset, info.test_split, shuffle=True)

	if 'val_split' in info: # use/create validation set
		assert 0 < info.val_split < 1, 'cant split: {}'.format(info.val_split)
		trainsets = simple_split_dataset(trainsets[0], info.val_split, shuffle=True)

	return (*trainsets, testset) # testset is None if it doesnt exist or has to be loaded separately (with mode=='test')



# old

def old_get_loaders(*datasets, batch_size=None, num_workers=0, shuffle=True, pin_memory=True,
		   drop_last=False, worker_init_fn=None, silent=False):

	if shuffle == 'all':
		shuffles = [True]*3
	elif shuffle:
		shuffles = [True, False, False]
	else:
		shuffles = [False]*3

	if batch_size is None:
		batch_size = 64 # TODO: maybe choose batch size smartly

	loaders = [DataLoader(ds, batch_size=batch_size, shuffle=s, num_workers=num_workers,
						  # collate_fn=_collate_movable,
						  pin_memory=pin_memory and not isinstance(ds, Device_Dataset), drop_last=drop_last,
						  worker_init_fn=worker_init_fn) for ds, s in zip(datasets, shuffles)]

	# print(loaders[0])

	if not silent:
		trainloader = loaders[0]
		testloader = None if len(loaders) < 2 else loaders[-1]
		valloader = None if len(loaders) < 3 else loaders[1]

		print('traindata len={}, trainloader len={}'.format(len(datasets[0]), len(trainloader)))
		if valloader is not None:
			print('valdata len={}, valloader len={}'.format(len(datasets[1]), len(valloader)))
		if testloader is not None:
			print('testdata len={}, testloader len={}'.format(len(datasets[-1]), len(testloader)))
		print('Batch size: {} samples'.format(batch_size))

	# if len(loaders) == 1:
	# 	return loaders[0]
	return loaders


_pytorch_toy_datasets = {
	'mnist': torchvision.datasets.MNIST,
	'kmnist': torchvision.datasets.KMNIST,
	'fmnist': torchvision.datasets.FashionMNIST,
	'emnist': torchvision.datasets.EMNIST,
	'svhn': torchvision.datasets.SVHN
}
_pytorch_toy_datasets_classes = {
	'emnist': 26,
	'svhn': 10,
}
_pytorch_toy_datasets_size = {
	'svhn': (3,32,32),
}

_pytorch_toy_dataset_args = {
	'emnist': {'split':'letters'},
}

_pytorch_toy_dataset_train_args = {
	'svhn': {'split':'train'},
}
_pytorch_toy_dataset_test_args = {
	'svhn': {'split':'test'},
}
def load_data(path=None, args=None): # DEPRECATED

	assert path is not None or args is not None, 'must specify the model'

	if path is not None:
		if os.path.isdir(path):
			path = os.path.join(path, 'best.pth.tar')
		assert os.path.isfile(path), 'Could not find encoder:' + path

		checkpoint = torch.load(path)

		if 'traindata' in checkpoint and 'testdata' in checkpoint:
			print('Loaded dataset from {}'.format(path))
			if 'valdata' in checkpoint:
				return checkpoint['traindata'], checkpoint['valdata'], checkpoint['testdata']
			return checkpoint['traindata'], checkpoint['testdata']

		print('Loaded args from {}'.format(path))
		args = checkpoint['args']

	if args.dataset in _pytorch_toy_datasets:

		args.save_datasets = False

		args.din = _pytorch_toy_datasets_size[args.dataset] if args.dataset in _pytorch_toy_datasets_size else (1, 28, 28)

		args.dout = _pytorch_toy_datasets_classes[args.dataset] if args.dataset in _pytorch_toy_datasets_classes else 10

		train_kwargs = _pytorch_toy_dataset_args[args.dataset] if args.dataset in _pytorch_toy_dataset_args else {}
		test_kwargs = train_kwargs.copy()

		if args.dataset in _pytorch_toy_dataset_train_args:
			train_kwargs.update(_pytorch_toy_dataset_train_args[args.dataset])
		else:
			train_kwargs['train'] = True
		if args.dataset in _pytorch_toy_dataset_train_args:
			test_kwargs.update(_pytorch_toy_dataset_test_args[args.dataset])
		else:
			test_kwargs['train'] = False

		traindata = _pytorch_toy_datasets[args.dataset](os.path.join(DEFAULT_DATA_PATH, args.dataset), download=True,
											   transform=torchvision.transforms.ToTensor(), **train_kwargs)
		testdata = _pytorch_toy_datasets[args.dataset](os.path.join(DEFAULT_DATA_PATH, args.dataset), download=True,
											  transform=torchvision.transforms.ToTensor(), **test_kwargs)

		if args.dataset == 'emnist': # they didnt use zero based indexing for the classes (!)
			traindata = Format_Dataset(traindata, format_fn=lambda xy: (xy[0].permute(0,2,1), xy[1]-1))
			testdata = Format_Dataset(testdata, format_fn=lambda xy: (xy[0].permute(0,2,1), xy[1] - 1))

		if hasattr(args, 'indexed') and args.indexed:
			traindata = Indexed_Dataset(traindata)
			testdata = Indexed_Dataset(testdata)

		if args.use_val:

			traindata, valdata = split_dataset(traindata, split1=1-args.val_per, shuffle=False)
			
			return traindata, valdata, testdata
		
		return traindata, testdata

	if 'hf' in args.dataset:

		args.save_datasets = True

		n = len(args.data)

		args.data_files = []

		# print('Removing half of the ambient data')
		for dd in args.data:
			new_files = [os.path.join(dd, df) for df in os.listdir(dd)]

			num = len(new_files)

			new_files = new_files#[:num // n]

			print('Found {} samples in {} using {}'.format(num, dd, len(new_files)))

			args.data_files.extend(new_files)

		fmt_fn = None
		if 'seq' in args.dataset:

			dataset = ConcatDataset([H5_Flattened_Dataset(d, keys={'rgbs'}) for d in args.data_files])
			fmt_fn = format_h5_seq

		else:
			dataset = ConcatDataset([H5_Dataset(d, keys={'rgbs'}) for d in args.data_files]) if len(args.data_files) > 1 else H5_Dataset(args.data_files[0], keys={'rgbs'})

			assert False

		if args.test_per is not None and args.test_per > 0:

			datasets = split_dataset(dataset, args.test_per)

		else:
			datasets = (dataset,)

		if args.use_val:

			valdata, traindata = split_dataset(datasets[-1], args.val_per, shuffle=False)

			datasets = datasets[0], valdata, traindata

		datasets = [Format_Dataset(ds, format_fn=fmt_fn) for ds in datasets]

		return datasets[::-1]


	# Failed
	raise Exception('Unknown dataset: {}'.format(args.dataset))


def format_h5_seq(raw):

	x = torch.from_numpy(util.str_to_rgb(raw['rgbs'])).permute(2,0,1).float() / 255

	return x,



