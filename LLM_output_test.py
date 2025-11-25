import os
from text_to_scene import text_to_scene, create_seed

def run_batch_without_subprocess(
    prompt,
    repeat_n=5,
    base_output_dir="batch_outputs"
):
    os.makedirs(base_output_dir, exist_ok=True)

    for i in range(repeat_n):
        run_dir = os.path.join(base_output_dir, f"run_{i:03d}")
        os.makedirs(run_dir, exist_ok=True)

        print(f"\n=== Running test {i+1}/{repeat_n} ===")
        print(f"Prompt: {prompt}")
        print(f"Saving to: {run_dir}")

        # 直接调用函数，不走 subprocess
        text_to_scene(
            input_prompt=prompt,
            model_name="deepseek-chat",
            plan_only=True,
            return_ego=True,
            save_dir=run_dir,
            use_cache=False
        )

    print("\nAll repeated tests done.")


if __name__ == "__main__":
    # 要测试的 prompt
    prompt_text = "A firetruck from the left road is coming when the ego car is turning right."

    # 重复次数 N
    repeat_times = 5

    run_batch_without_subprocess(prompt_text, repeat_times)
