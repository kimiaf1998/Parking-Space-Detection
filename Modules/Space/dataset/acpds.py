import json
from time import time
import multiprocessing as mp

import numpy
import torch
import torchvision
import torchvision.transforms as T
import torchvision.transforms.functional as TF

from torch.utils.data import DataLoader
from functools import lru_cache

from models.utils.pooling import convert_points_2_two, calculate_rectangular_coordinates
from utils_funcs import utils, transforms
from pycocotools.coco import COCO


class ACPDS():
    """
    A basic dataset of parking lot images,
    parking space coordinates (ROIs), and occupancies.
    Returns the tuple (image, rois, occupancy).
    """

    def __init__(self, dataset_path, ds_type='train', res=None, transform=None):
        self.dataset_path = dataset_path
        self.ds_type = ds_type
        self.res = res
        self.transform = transform

        # load all annotations
        with open(f'{self.dataset_path}/{ds_type}.json', 'r') as f:
            annotations = json.load(f)

        # # select train, valid, test, or all annotations
        # if ds_type in ['train', 'valid', 'test']:
        #     # select train, valid, or test annotations
        #     annotations = all_annotations[ds_type]
        # else:
        #     # select all annotations
        #     assert ds_type == 'all'
        #     # if using all annotations, combine the train, valid, and test dicts
        #     annotations = {k: [] for k in all_annotations['train'].keys()}
        #     for ds_type in ['train', 'valid', 'test']:
        #         for k, v in all_annotations[ds_type].items():
        #             annotations[k] += v
        self.coco = COCO(f'{self.dataset_path}/{ds_type}.json')
        self.images = annotations['images']
        self.annotations = annotations['annotations']
        self.img_ids = self.coco.getImgIds()
        self.cat_ids = self.coco.getCatIds()
        self.cat2label = {
            cat_id: i + 1
            for i, cat_id in enumerate(self.cat_ids)
        }

    @lru_cache(maxsize=None)
    def __getitem__(self, idx):
        # load image
        image_path = f'{self.dataset_path}/images/{self.images[idx]["file_name"]}'
        image = torchvision.io.read_image(image_path)

        img_id = self.img_ids[idx]
        ann_ids = self.coco.getAnnIds(imgIds=img_id)
        ann_info = self.coco.loadAnns(ann_ids)

        # load annotations
        boxes, labels, masks, areas = [], [], [], []
        for ann in ann_info:
            boxes.append(ann['bbox'])
            labels.append(ann['category_id'])
            areas.append(ann['area'])
            masks.append(self.coco.annToMask(ann))

        # rois = convert_points_2_two(rois)
        boxes = torch.as_tensor(boxes, dtype=torch.float32)
        masks = torch.as_tensor(numpy.array(masks), dtype=torch.uint8)
        areas = torch.as_tensor(areas)
        labels = torch.tensor(labels)

        # Suppose all instances are not crowd
        iscrowd = torch.zeros((boxes.shape[0],), dtype=torch.int64)

        target = {}
        target["boxes"] = boxes
        target["masks"] = masks
        target["labels"] = labels
        target["area"] = areas
        target["iscrowd"] = iscrowd
        target["image_id"] = torch.tensor([idx])

        # Filter out small boxes
        target = filter_small_areas(target, threshold=3200)

        if self.res is not None:
            resize_transform = transforms.Resize(self.res)
            image, target = resize_transform(image=image,
                                           target=target)

        if self.transform:
            image, target = self.transform(image=image,
                                           target=target)

        return image, target

    def __len__(self):
        return len(self.images)


def collate_fn(batch):
    images = [item[0] for item in batch]
    targets = [item[1] for item in batch]
    return [images, targets]


def create_datasets(dataset_path, batch_size, *args, **kwargs):
    """
    Create training and test DataLoaders.
    Returns the tuple (image, rois, occupancy).
    During the first pass, the DataLoaders will be cached.
    """
    ds_train = ACPDS(dataset_path, 'train', *args, **kwargs)
    ds_valid = ACPDS(dataset_path, 'valid', *args, **kwargs)
    ds_test = ACPDS(dataset_path, 'test', *args, **kwargs)

    data_loader_train = DataLoader(ds_train, batch_size=batch_size, shuffle=True, collate_fn=utils.collate_fn)
    data_loader_valid = DataLoader(ds_valid, batch_size=batch_size, shuffle=False, collate_fn=utils.collate_fn)
    data_loader_test = DataLoader(ds_test, batch_size=batch_size, shuffle=False, collate_fn=utils.collate_fn)
    return data_loader_train, data_loader_valid, data_loader_test


def get_all_possible_num_of_workers(ds):
    for num_workers in range(2, mp.cpu_count(), 2):
        train_loader = DataLoader(ds, shuffle=True, num_workers=num_workers, batch_size=64, pin_memory=True)
        start = time()
        for epoch in range(1, 3):
            for i, data in enumerate(train_loader, 0):
                pass
        end = time()
        print("Finish with:{} second, num_workers={}".format(end - start, num_workers))


def filter_small_areas(target,  threshold):
    boxes, masks = [], []
    for box, mask, area in zip(target['boxes'], target['masks'], target['area']):
        if area.item() > threshold:
            boxes.append(box.tolist())
            masks.append(mask.tolist())
    target['boxes'] = torch.as_tensor(boxes, dtype=torch.float32)
    target['masks'] = torch.as_tensor(numpy.array(masks), dtype=torch.uint8)
    return target