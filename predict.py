# -*- coding: utf-8 -*-
import os
import numpy as np
import random
import shutil
from PIL import Image
import skimage
from statistics import mode

from colorthief import ColorThief
from keras.optimizers import *
from keras.utils import plot_model
from keras.applications import VGG16
from keras.applications.vgg16 import preprocess_input
from keras.models import Model, model_from_json, load_model
from keras.layers import *
from sklearn.cluster import AffinityPropagation, KMeans
from sklearn.metrics import pairwise_distances_argmin_min
import matplotlib.pyplot as plt
from utils import init_globals, get_image_paths, draw_rect
from train import create_model
from segmentation import selective_search_aggregated, cluster_bboxes, selective_search_bbox_fast
import logging
logging.basicConfig(level=logging.INFO, format="[%(lineno)4s : %(funcName)-30s ] %(message)s")

### GLOBALS
batch_size = 64
img_width = 224             # For VGG16
img_height = 224            # For VGG16
img_channel = 3
prediction_path = '../prediction/'
results_path = os.path.join(prediction_path, 'results')
fashion_dataset_path='../Data/fashion_data/'

def intersection_area(boxes1, boxes2):
    x11, y11, x12, y12 = boxes1[0], boxes1[1], boxes1[2], boxes1[3]
    x21, y21, x22, y22 = boxes2[0], boxes2[1], boxes2[2], boxes2[3]
    xA = np.maximum(x11, x21)
    yA = np.maximum(y11, y21)
    xB = np.minimum(x12, x22)
    yB = np.minimum(y12, y22)
    return np.maximum((xB - xA + 1), 0) * np.maximum((yB - yA + 1), 0)

def get_palette(name, num_colors=5):
    color_thief = ColorThief(name)
    cf_palette = [(x[0], x[1], x[2]) for x in color_thief.get_palette(color_count=num_colors)]
    closest, _ = pairwise_distances_argmin_min(cf_palette, colors)
    cl_names = [color_names[i] for i in closest]
    return cl_names

def get_crops_resized(image_path_name, bboxeswh):
    img = Image.open(image_path_name)
    img_crops = []
    dims = []
    for index, bboxwh in enumerate(bboxeswh):
        x1, y1, x2, y2 = bboxwh[0], bboxwh[1], bboxwh[2]+bboxwh[0], bboxwh[3]+bboxwh[1]
        img_crop = img.crop((x1, y1, x2, y2))
        dims.append((x1, y1, img_crop.size[0], img_crop.size[1]))
        img_crop = img_crop.resize((img_width, img_height))
        img_crop = np.array(img_crop).astype(np.float32)
        img_crops.append(img_crop)
    return (img_crops, dims)

def display(image_path_name, width, height, bboxeswh, prediction_iou, prediction_class_name, prediction_class_prob, prediction_attr_names,
            prediction_attr_probs, prediction_bbox):
    thres = 50
    true_bboxes = []
    true_frames = []
    bbox_probs = []
    cls_nm = []
    cls_probs = []
    attr_nm = []
    attr_probs = []
    for i in range(len(prediction_bbox)):
        if prediction_class_prob[i]*100 >= thres and len(prediction_attr_probs[i])>0 or i==len(bboxeswh)-1:
            w1, h1 = prediction_bbox[i][2] - prediction_bbox[i][0], prediction_bbox[i][3] - prediction_bbox[i][1]
            if w1 * h1 < (width * height) / 40:
                print('removed for size: ', prediction_bbox[i])
                continue
            for y in prediction_bbox:
                w2, h2 = y[2] - y[0], y[3] - y[1]
                if y != prediction_bbox[i] and intersection_area(prediction_bbox[i], y) > 0.5*w1*h1 and w2*h2 > w1*h1:
                    print('removed for intersect: ', prediction_bbox[i])
                    break
            else:
                true_bboxes.append(prediction_bbox[i])
                true_frames.append(bboxeswh[i])
                bbox_probs.append(prediction_iou[i])
                cls_probs.append(prediction_class_prob[i])
                cls_nm.append(prediction_class_name[i])
                attr_nm.append(prediction_attr_names[i])
                attr_probs.append(prediction_attr_probs[i])
    bbox_probs = np.array(bbox_probs)
    cls_probs = np.array(cls_probs)
    attr_probs = np.array(attr_probs)
    true_bboxes = np.array(true_bboxes)
    true_frames = np.array(true_frames)
    # np_true_bboxes_scaled = np.array([[bb[0] / width, bb[1] / height, bb[2] / width, bb[3] / height] for bb in true_bboxes])
    np_true_bboxes_scaled = np.array([[(bb[0]+bb[2]/2)/width, (bb[1]+bb[3]/2)/height] for bb in true_frames])
    bbox_centers_colors = np.zeros((len(np_true_bboxes_scaled), 3))
    bbox_colors = np.zeros((len(np_true_bboxes_scaled), 3))
    bbox_colors[:,0] = 1
    answer = []
    if len(true_bboxes) > 1:
        af = AffinityPropagation(preference=-0.05).fit(np_true_bboxes_scaled)
        labels = af.labels_
        frame_sizes = [x[2] * x[3] for x in true_frames]
        for cluster in np.unique(labels):
            frame_sizes_cluster = [x[2] * x[3] for x in true_frames[labels == cluster]]
            max_size_frame_index = np.argwhere(frame_sizes == np.max(frame_sizes_cluster))[0][0]
            cluster_color = np.random.rand(3,)
            bbox_centers_colors[labels == cluster] = cluster_color
            index = max_size_frame_index
            answer.append(((true_bboxes[index][0], true_bboxes[index][1], true_bboxes[index][2], true_bboxes[index][3]), bbox_probs[index], cls_nm[index], cls_probs[index], attr_nm[index], attr_probs[index]))
    else:
        if len(true_bboxes) == 1:
            answer.append(((true_bboxes[0][0], true_bboxes[0][1], true_bboxes[0][2], true_bboxes[0][3]), bbox_probs[0], cls_nm[0], cls_probs[0], attr_nm[0], attr_probs[0]))

    # candids = list(answer)
    # for x in answer:
    #     w, h = x[0][2] - x[0][0], x[0][3] - x[0][1]
    #     if w*h < (width*height)/40:
    #         candids.remove(x)
    #         print('removed for size: ', x)
    #         continue
    #     for y in answer:
    #         if y != x and intersection_area(x[0], y[0]) > 0.5*w*h and y[6] > x[6]:
    #             candids.remove(x)
    #             print('removed for intersect: ', x)
    #             break

    # fig, axes = plt.subplots(1, 3, figsize=(8, 5), frameon=False)
    # ax1 = axes[0]
    # ax2 = axes[1]
    # ax3 = axes[2]
    # xlabel_ax1 = []
    # img1 = Image.open(image_path_name)
    # img2 = Image.open(image_path_name)
    # img3 = Image.open(image_path_name)
    # # 11111111111111111111111111111111111111111111111111111111111111111111111111111111111111
    # # for i, bbox in enumerate(true_bboxes):
    # #     x, y, w, h = bbox[0], bbox[1], (bbox[2] - bbox[0]), (bbox[3] - bbox[1])
    # #     draw_rect(ax1, img1, (x, y, w, h), '%s %.2s%%'%(cls_nm[i],cls_probs[i]*100), edgecolor=bbox_colors[i])
    # #     ax1.plot(x + w/2, y + h/2, c=bbox_centers_colors[i], marker='o')
    # # ax1.imshow(img1)
    # for i, bbox in enumerate(bboxeswh):
    #     x, y, w, h = bbox[0], bbox[1], bbox[2], bbox[3]
    #     draw_rect(ax1, img1, (x, y, w, h), edgecolor=np.random.rand(3,))
    # ax1.imshow(img1)
    # ax1.set_xlabel(image_path_name)
    # # 2222222222222222222222222222222222222222222222222222222222222222222222222222222222222222222
    # tags = []
    # for i, bbox in enumerate(true_frames):
    #     # x, y, w, h = bbox[0], bbox[1], (bbox[2] - bbox[0]), (bbox[3] - bbox[1])
    #     x, y, w, h = bbox[0], bbox[1], bbox[2], bbox[3]
    #     draw_rect(ax2, img2, (x, y, w, h), '%s %.2s%%'%(cls_nm[i],cls_probs[i]*100), edgecolor=bbox_colors[i])
    #     ax2.plot(x + w/2, y + h/2, c=bbox_centers_colors[i], marker='o')
    # ax2.imshow(img2)
    # 3333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333
    img0 = Image.open(image_path_name)
    img00 = Image.open(image_path_name)
    fig0 = plt.figure(figsize=(5, 5), frameon=False)
    fig0.set_size_inches(5, 5)
    ax0 = plt.Axes(fig0, [0., 0., 1., 1.])
    ax0.set_axis_off()
    fig0.add_axes(ax0)
    with open(os.path.join(results_path,'anno.txt'), 'a') as f:
        for bbox,bbox_prob,cls_name,cls_prob,attr_name,attr_prob in answer:
            x, y, w, h = bbox[0], bbox[1], (bbox[2] - bbox[0]), (bbox[3] - bbox[1])
            # draw_rect(ax3, img3, (x, y, w, h), cls_name, textcolor=(0, 255, 0))
            # ax3.plot(x + w/2, y + h/2, 'ro')
            draw_rect(ax0, img0, (x, y, w, h), cls_name, textcolor=(0, 255, 0))
            attr_probs = sorted(attr_prob, reverse=True)
            tags=','.join([attr_name[np.argwhere(attr_prob == x)[0][0]] for x in attr_probs])
            palette=','.join(get_palette(img00.crop((bbox[0],bbox[1],bbox[2],bbox[3]))))
            f.write('{} {} {}\n'.format(os.path.split(image_path_name)[1], tags, palette))
    # ax3.imshow(img3, aspect='equal')
    # ax3.set_xlabel('\n'.join(tags))
    # plt.show()
    fig0.savefig(os.path.join(results_path, os.path.split(image_path_name)[1]))

### MAIN ###
if __name__ == '__main__':
    global class_names, input_shape, attr_names, attr_names_RU, class_names_RU, class35, attr200, colors, color_names
    class_names, input_shape, attr_names = init_globals()
    class35 = ['Blazer', 'Top', 'Dress', 'Chinos', 'Jersey', 'Cutoffs', 'Kimono', 'Cardigan', 'Jeggings', 'Button-Down',
               'Romper', 'Skirt', 'Joggers', 'Tee', 'Turtleneck', 'Culottes', 'Coat', 'Henley', 'Jeans', 'Hoodie',
               'Blouse',
               'Tank', 'Shorts', 'Bomber', 'Jacket', 'Parka', 'Sweatpants', 'Leggings', 'Flannel', 'Sweatshorts',
               'Jumpsuit', 'Poncho', 'Trunks', 'Sweater', 'Robe']
    attr200 = [730, 365, 513, 495, 836, 596, 822, 254, 884, 142, 212, 883, 837, 892, 380, 353, 196, 546, 335, 162, 441,
               717,
               760, 568, 310, 705, 745, 81, 226, 830, 620, 577, 1, 640, 956, 181, 831, 720, 601, 112, 820, 935, 969,
               358,
               933, 983, 616, 292, 878, 818, 337, 121, 236, 470, 781, 282, 913, 93, 227, 698, 268, 61, 681, 713, 239,
               839,
               722, 204, 457, 823, 695, 993, 0, 881, 817, 571, 565, 770, 751, 692, 593, 825, 574, 50, 207, 186, 237,
               563,
               300, 453, 897, 944, 438, 688, 413, 409, 984, 191, 697, 368, 133, 676, 11, 754, 800, 83, 14, 786, 141,
               841,
               415, 608, 276, 998, 99, 851, 429, 287, 815, 437, 747, 44, 988, 249, 543, 560, 653, 843, 208, 899, 321,
               115,
               887, 699, 15, 764, 48, 749, 852, 811, 862, 392, 937, 87, 986, 129, 336, 689, 245, 911, 309, 775, 638,
               184,
               797, 512, 45, 682, 139, 306, 880, 231, 802, 264, 648, 410, 30, 356, 531, 982, 116, 599, 774, 900, 218,
               70,
               562, 108, 25, 450, 785, 877, 18, 42, 624, 716, 36, 920, 423, 784, 788, 538, 325, 958, 480, 20, 38, 931,
               666,
               561]
    class_names_RU = []
    with open(os.path.join(fashion_dataset_path, 'Anno/1_list_category_cloth.txt'), encoding='utf-8') as f:
        next(f)
        next(f)
        for line in f:
            class_names_RU.append(line.split()[2])
    attr_names_RU = []
    with open(os.path.join(fashion_dataset_path, 'Anno/2_list_attr_cloth.txt'), encoding='utf-8') as f:
        next(f)
        next(f)
        for line in f:
            lines = line.split()
            for i in range(len(lines)):
                if lines[i].isdigit():
                    break
            attr_names_RU.append('-'.join(lines[i+1:]))
    color_names = []
    colors = []
    with open('../Data/color_table.txt') as f:
        for line in f:
            line = line.split()
            r, g, b = line[1][:2], line[1][2:4], line[1][4:]
            color_names.append(line[0]+'-#'+line[1])
            colors.append([int(r, 16), int(g, 16), int(b, 16)])
    colors = np.array(colors)

    # if os.path.exists(results_path):
        # shutil.rmtree(results_path) # quationable
    # os.makedirs(results_path)

    # base_model = VGG16(weights='imagenet', include_top=False, input_shape=input_shape)
    model = load_model('models/full_model.h5')
    for index, img_path in enumerate(get_image_paths(prediction_path)):
        image = skimage.io.imread(img_path)
        w, h = image.shape[1], image.shape[0]
        # bboxeswh = cluster_bboxes(selective_search_bbox_fast(image, w*h/50), w, h, -0.15)
        bboxeswh = cluster_bboxes(selective_search_bbox_fast(np.array(image), (w * h) / 40), w, h, preference=-0.35)
        image_crops, dims = get_crops_resized(img_path, bboxeswh)
        img = Image.open(img_path)
        img = img.resize((img_width, img_height))
        img = np.array(img).astype(np.float32)
        image_crops.append(img)
        dims.append((0, 0, w, h))
        bboxeswh.append([0, 0, w, h])
        images_list = preprocess_input(np.array(image_crops))
        # predictions = base_model.predict(images_list, batch_size)
        bboxes, attrs, classes = model.predict(images_list, batch_size, verbose=1)
        prediction_iou = []
        prediction_bbox = []
        prediction_attr_probs = []
        prediction_attr_names = []
        prediction_class_prob = []
        prediction_class_name = []
        for i, t in enumerate(zip(bboxes, classes, attrs)):
            pred_bbox, pred_cls, pred_attr = t[0], t[1], t[2]
            prediction_iou.append(pred_bbox[4])
            prediction_bbox.append((dims[i][0] + pred_bbox[0]*dims[i][2], dims[i][1] + pred_bbox[1]*dims[i][3], dims[i][0] + pred_bbox[2]*dims[i][2],dims[i][1] +  pred_bbox[3]*dims[i][3]))
            prediction_class_prob.append(np.max(pred_cls))
            prediction_class_name.append(class_names_RU[class_names.index(class35[np.argmax(pred_cls)])])
            prediction_attr_probs.append([x for x in pred_attr if x>= 0.5])
            prediction_attr_names.append([attr_names_RU[attr200[i]] for i in range(len(pred_attr)) if pred_attr[i] >= 0.5])
        display(img_path, w, h, bboxeswh, prediction_iou, prediction_class_name, prediction_class_prob, prediction_attr_names,
                prediction_attr_probs, prediction_bbox)
    a=2