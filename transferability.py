import numpy as np
import json
import os
import sys
import time
import math
import io
import torch
import torch.nn as nn
import torch.optim as optim
from torchvision import models
import torchvision.datasets as dsets
import torchvision.transforms as transforms
from torchattacks.attack import Attack
from utils import *
from compression import *
from decompression import *
from PIL import ImageFile
from info_attack import InfoDrop
from Models.transformers import diet_tiny, diet_small, vit_tiny, vit_small


from PIL import ImageFile
import lpips
import torch
import torch.nn as nn
import torch.nn.functional as F
ImageFile.LOAD_TRUNCATED_IMAGES = True
from timm.data import resolve_data_config
from timm.data.transforms_factory import create_transform

source_model = ["resnet"]
target_model = ["resnet18"]


model_t = ["resnet", vit_tiny, vit_small, diet_tiny, diet_small]
q_sizes = [20, 60, 100]
attacks = [False]
model_names = ["ResNet50", "ViT_tiny", "ViT_small", "DieT_tiny", "DieT_small"]

"""
Source s_model: Model on which the attack would be done, adversarial images
will be generated using this s_model.
target s_model: Model on which the adversarial images would be tested

"""

class Normalize(nn.Module):
    def __init__(self, mean, std):
        super(Normalize, self).__init__()
        self.register_buffer('mean', torch.Tensor(mean))
        self.register_buffer('std', torch.Tensor(std))

    def forward(self, input):
        # Broadcasting
        input = input / 255.0
        mean = self.mean.reshape(1, 3, 1, 1)
        std = self.std.reshape(1, 3, 1, 1)
        return (input - mean.to(device=input.device)) / std.to(
            device=input.device)
def pred_label_and_confidence(model, input_batch, labels_to_class):
    input_batch = input_batch.cuda()
    with torch.no_grad():
        out = model(input_batch)
    _, index = torch.max(out, 1)

    percentage = torch.nn.functional.softmax(out, dim=1) * 100
    # print(percentage.shape)
    pred_list = []
    for i in range(index.shape[0]):
        pred_class = labels_to_class[index[i]]
        pred_conf = str(round(percentage[i][index[i]].item(), 2))
        pred_list.append([pred_class, pred_conf])
    return pred_list

def lpips_2imgs(img_batch0, img_batch1, version="0.1", use_gpu=True):
    loss_fn = lpips.LPIPS(net='alex', version=version)

    if (use_gpu):
        loss_fn.cuda()

    # img0 = lpips.im2tensor(lpips.load_image(path0))  # RGB image from [-1,1]
    # img1 = lpips.im2tensor(lpips.load_image(path1))

    if (use_gpu):
        img_batch0 = img_batch0.cuda()
        img_batch1 = img_batch1.cuda()
    dist01 = loss_fn.forward(img_batch0, img_batch1)
    # print('Distance: %.3f' % dist01)
    return dist01


def build_model(model, adver=False):
    """
    Builds the model and the norm layer
    adver=True means the adversarial images will be passed through the model,
    no need of resizing for that case.
    """
    if model == "resnet":
        transform = transforms.Compose([
                                        transforms.Resize((224, 224)),
                                        transforms.ToTensor(), ]
                                        ) \
            if not adver else transforms.Compose([transforms.ToTensor(),])
        norm_layer = Normalize(mean=[0.485, 0.456, 0.406],
                               std=[0.229, 0.224, 0.225])
        backbone = models.resnet50(pretrained=True)

    elif model == "resnet18":
        transform = transforms.Compose([
                                        transforms.Resize((224, 224)),
                                        transforms.ToTensor(), ]
                                        ) \
            if not adver else transforms.Compose([transforms.ToTensor(),])
        norm_layer = Normalize(mean=[0.485, 0.456, 0.406],
                               std=[0.229, 0.224, 0.225])
        backbone = models.resnet18(pretrained=True)

    else:

        backbone = model()
        config = resolve_data_config({}, model=backbone)
        transform = create_transform(**config)
        transform.transforms.pop()
        transform = transform if not adver else transforms.Compose([transforms.ToTensor(),])
        norm_layer = Normalize(mean=config['mean'],
                               std=config['std'])
    return backbone, norm_layer, transform

if __name__ == "__main__":
    f = open("results/transferability.txt", "w")

    for att in attacks:
        idx_ = 0
        targetted_attack = att
        for s, t in zip(source_model, target_model):


            if targetted_attack:
                 name = model_names[idx_]+"_targetted"
            else:
                name = model_names[idx_]+ "_untargetted"
            print(f"{idx_}::: model_name: {name}")
            for q_size in q_sizes:

                device = torch.device(
                    "cuda" if torch.cuda.is_available() else "cpu")
                class_idx = json.load(open("./imagenet_class_index.json"))
                idx2label = [class_idx[str(k)][1] for k in range(len(class_idx))]
                class2label = [class_idx[str(k)][0] for k in
                               range(len(class_idx))]

                backbone_s, norm_layer_s, transform_s = build_model(s, adver=False)
                backbone_t, norm_layer_t, transform_t = build_model(t, adver=True)

                s_model = nn.Sequential(norm_layer_s, backbone_s.to(device))
                s_model = s_model.eval()
                s_model_name = name

                t_model = nn.Sequential(norm_layer_t, backbone_t.to(device))
                t_model = t_model.eval()

                batch_size = 20
                q_size = q_size
                cur_cnt = 0
                suc_cnt = 0
                data_dir = "./test-data"
                save_dir = "./results"
                data_clean(data_dir)
                normal_data = image_folder_custom_label(root=data_dir,
                                                        transform=transform_s,
                                                        idx2label=class2label)
                normal_loader = torch.utils.data.DataLoader(normal_data,
                                                            batch_size=batch_size,
                                                            shuffle=False)

                i = 0
                fool_rate = 0
                file_number = 0
                lpips_score = 0

                for i, (images, labels) in enumerate(normal_loader):  # in range(tar_cnt//batch_size):

                    print("Iter: ", i)
                    gt_labels = labels
                    if targetted_attack:
                        labels = torch.from_numpy(np.random.randint(0, 1000, size=images.shape[0]))

                    images = images * 255.0
                    steps = 500 if targetted_attack else 50   # change again from 20 to 500
                    attack = InfoDrop(s_model, batch_size=images.shape[0],
                                      q_size=q_size, steps=steps,
                                      targeted=targetted_attack)
                    # Add unclamped adv images
                    at_images, at_labels, suc_step, at_images_unclamped = attack(images, labels)

                    ### Calculate fool rate on the target s_model

                    labels_before_attack = t_model(images.to(device="cuda"))
                    _, labels_before_attack = torch.max(labels_before_attack.data, 1)

                    labels_after_attack = t_model(at_images_unclamped.to(device="cuda"))
                    _, labels_after_attack = torch.max(
                        labels_after_attack.data, 1)
                    if (labels_after_attack == at_labels).sum() != 20:
                        print("different results")
                    fool_rate += torch.sum(labels_before_attack != labels_after_attack)


                    # outputs_pre_attack = s_model(images.to(device="cuda"))
                    # _, pred_pre_attack_label = torch.max(outputs_pre_attack.data,
                    #                                      1)
                    # fool_rate += torch.sum(pred_pre_attack_label != at_labels)

                    dist = lpips_2imgs(at_images.to(device="cuda"), images.to(device="cuda"))
                    dist = dist.sum()/at_images.shape[0]
                   # print(f"Avg Sim Batch {dist}")
                    lpips_score += dist

                    labels = labels.to(device)
                    # Success rate of adversarial examples on the target model
                    # check target labels with the predictions of the target model
                    if targetted_attack:
                        suc_cnt += (labels_after_attack == labels).sum().item()
                    else:
                        suc_cnt += (labels_after_attack != labels).sum().item()
                    print("Current suc. rate: ", suc_cnt / ((i + 1) * batch_size))

                score_list = np.zeros(len(normal_data))
                score_list[:suc_cnt] = 1.0
                stderr_dist = np.std(np.array(score_list)) / np.sqrt(
                    len(score_list))
                print('Avg suc rate: %.5f +/- %.5f' % (
                suc_cnt / len(normal_data), stderr_dist))
                print(f"Fool Rate {q_size} is : {fool_rate / len(normal_data)}")
                print(f"Average Similarity score: {lpips_score / len(normal_loader)}")
                f.write(
                    f"{name}_{q_size},{(suc_cnt / len(normal_data))}, {stderr_dist}, {fool_rate / len(normal_data)}, {lpips_score / len(normal_loader)} \n")
            idx_ += 1

    f.close()

